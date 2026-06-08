import { useState, useCallback } from 'react'
import MapView from './MapView'
import { fetchJson } from '../api'
import './CorridorSimulation.css'

const DRONE_TYPES = [
    'DJI Mini 3 Pro',
    'DJI Mavic 3',
    'DJI Matrice 300 RTK',
    'DJI Inspire 3',
    'Custom (Generic)'
]

function normalizeSourceChain(sourceChain) {
    if (Array.isArray(sourceChain)) {
        return sourceChain.map(item => String(item).trim()).filter(Boolean)
    }

    if (typeof sourceChain === 'string' && sourceChain.trim()) {
        return sourceChain
            .split(' + ')
            .map(item => item.trim())
            .filter(Boolean)
    }

    return []
}

function formatSourceChain(sourceChain) {
    const normalized = normalizeSourceChain(sourceChain)
    return normalized.length ? normalized.join(' · ') : '-'
}

function CorridorSimulation({ apiBaseUrl = '' }) {
    // 출발/도착 위치
    const [pointA, setPointA] = useState(null)
    const [pointB, setPointB] = useState(null)
    const [selectingPoint, setSelectingPoint] = useState(null) // 'A', 'B', or null

    // 분석 결과
    const [analysis, setAnalysis] = useState(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState(null)

    // 설정
    const [settings, setSettings] = useState({
        altitude: 50,
        segmentCount: 5,
        droneType: 'DJI Mavic 3'
    })

    // 지도 클릭 핸들러
    const handleMapClick = useCallback((lat, lon) => {
        if (selectingPoint === 'A') {
            setPointA({ lat, lon })
            setSelectingPoint(null)
            setAnalysis(null)
        } else if (selectingPoint === 'B') {
            setPointB({ lat, lon })
            setSelectingPoint(null)
            setAnalysis(null)
        }
    }, [selectingPoint])

    // 경로 분석 실행
    const analyzeRoute = async () => {
        if (!pointA || !pointB) {
            setError('출발점과 도착점을 모두 선택해주세요')
            return
        }

        setLoading(true)
        setError(null)

        try {
            const data = await fetchJson(apiBaseUrl, '/api/corridor-analysis', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    point_a: pointA,
                    point_b: pointB,
                    altitude: settings.altitude,
                    segment_count: settings.segmentCount,
                    drone_type: settings.droneType
                })
            })

            setAnalysis(data)
        } catch (err) {
            // 백엔드 연결 실패 시 모의 데이터
            const mockAnalysis = generateMockAnalysis()
            setAnalysis(mockAnalysis)
        } finally {
            setLoading(false)
        }
    }

    // 모의 분석 데이터 생성 (백엔드 미구현 대비)
    const generateMockAnalysis = () => {
        const distance = pointA && pointB
            ? Math.sqrt(Math.pow((pointB.lat - pointA.lat) * 111000, 2) + Math.pow((pointB.lon - pointA.lon) * 88000, 2))
            : 1000

        const routeSeed = Math.abs(
            Math.sin((pointA.lat + pointA.lon + pointB.lat + pointB.lon + settings.altitude) * 1000)
        )
        const segments = []
        const weatherSourceChain = ['browser_fallback', 'surface_only']

        for (let i = 0; i < settings.segmentCount; i++) {
            const riskLevel = (Math.sin((i + 1) * 12.9898 + routeSeed * 78.233) + 1) / 2
            let status = 'GO'
            if (riskLevel > 0.7) status = 'NO-GO'
            else if (riskLevel > 0.4) status = 'RESTRICT'
            const buildingConfidence = Number((0.45 + (1 - riskLevel) * 0.4).toFixed(2))
            const buildingSourceChain = riskLevel > 0.65
                ? ['browser_fallback', 'coordinate_based']
                : ['browser_fallback', 'surface_only']

            segments.push({
                id: i + 1,
                start_percent: (i / settings.segmentCount) * 100,
                end_percent: ((i + 1) / settings.segmentCount) * 100,
                status,
                wind_speed: (riskLevel * 8 + 2).toFixed(1),
                building_height: Math.floor(riskLevel * 30 + 10),
                building_floors: Math.max(1, Math.round((riskLevel * 30 + 10) / 3.3)),
                building_confidence: buildingConfidence,
                building_source: 'browser_fallback',
                building_profile_source: 'browser_synthetic',
                building_source_chain: buildingSourceChain,
                building_canyon_weight: Number((0.35 + 0.65 * buildingConfidence * 0.5).toFixed(3)),
                fcanyon_raw: Number((1 + 0.3 * Math.min(Math.floor(riskLevel * 30 + 10) / 12, 3)).toFixed(2)),
                fcanyon_effective: Number((1 + 0.3 * Math.min(Math.floor(riskLevel * 30 + 10) / 12, 3) * (0.35 + 0.65 * buildingConfidence * 0.5)).toFixed(2)),
                weather_source: 'browser_fallback',
                weather_source_chain: weatherSourceChain,
                weather_profile_source: 'surface_only',
                reason: status === 'GO' ? '안전 통과 가능' :
                    status === 'RESTRICT' ? '주의 필요 (건물 근접)' : '비행 불가 (고층 건물)'
            })
        }

        const goCount = segments.filter(s => s.status === 'GO').length
        const restrictCount = segments.filter(s => s.status === 'RESTRICT').length
        const nogoCount = segments.filter(s => s.status === 'NO-GO').length

        let overall = 'GO'
        if (nogoCount > 0) overall = 'NO-GO'
        else if (restrictCount > settings.segmentCount / 2) overall = 'RESTRICT'

        return {
            distance_m: Math.round(distance),
            flight_time_min: Math.round(distance / 10 / 60 * 10) / 10,
            overall_judgment: overall,
            segments,
            recommended_altitude: settings.altitude + (overall === 'NO-GO' ? 20 : 0),
            alternative_route: overall === 'NO-GO' ? '우회 경로 권장' : null,
            weather_source: 'browser_fallback',
            weather_source_chain: weatherSourceChain,
            weather_profile_source: 'surface_only',
            source_chain: weatherSourceChain,
            stale_cache: false
        }
    }

    // 위험도 색상
    const getStatusColor = (status) => {
        switch (status) {
            case 'GO': return '#22c55e'
            case 'RESTRICT': return '#eab308'
            case 'NO-GO': return '#ef4444'
            default: return '#6b7280'
        }
    }

    const getStatusEmoji = (status) => {
        switch (status) {
            case 'GO': return '🟢'
            case 'RESTRICT': return '🟡'
            case 'NO-GO': return '🔴'
            default: return '⚪'
        }
    }

    // 지도 센터 (선택된 지점 기준)
    const mapCenter = pointA || { lat: 37.5665, lon: 126.9780 }

    return (
        <div className="corridor-simulation">
            <div className="corridor-content">
                {/* 좌측: 지도 */}
                <section className="corridor-map-section">
                    <div className="map-header">
                        <h3>🗺️ 경로 설정</h3>
                        <p>지도를 클릭하여 출발점(A)과 도착점(B)을 선택하세요</p>
                    </div>

                    <div className="point-selection">
                        <button
                            className={`point-btn ${selectingPoint === 'A' ? 'active' : ''} ${pointA ? 'selected' : ''}`}
                            onClick={() => setSelectingPoint('A')}
                        >
                            🅰️ 출발점 {pointA ? `(${pointA.lat.toFixed(4)}, ${pointA.lon.toFixed(4)})` : '(미선택)'}
                        </button>
                        <span className="arrow">→</span>
                        <button
                            className={`point-btn ${selectingPoint === 'B' ? 'active' : ''} ${pointB ? 'selected' : ''}`}
                            onClick={() => setSelectingPoint('B')}
                        >
                            🅱️ 도착점 {pointB ? `(${pointB.lat.toFixed(4)}, ${pointB.lon.toFixed(4)})` : '(미선택)'}
                        </button>
                    </div>

                    {selectingPoint && (
                        <div className="selection-hint">
                            👆 지도를 클릭하여 {selectingPoint === 'A' ? '출발점' : '도착점'}을 선택하세요
                        </div>
                    )}

                    <div className="map-wrapper" style={{ height: '400px' }}>
                        <MapView
                            lat={mapCenter.lat}
                            lon={mapCenter.lon}
                            onLocationSelect={handleMapClick}
                            pointA={pointA}
                            pointB={pointB}
                            showPrimaryMarker={false}
                        />
                    </div>
                </section>

                {/* 우측: 설정 및 결과 */}
                <section className="corridor-panel-section">
                    {/* 설정 */}
                    <div className="settings-panel">
                        <h3>⚙️ 비행 설정</h3>

                        <div className="setting-row">
                            <label>비행 고도:</label>
                            <input
                                type="number"
                                value={settings.altitude}
                                onChange={e => setSettings(s => ({ ...s, altitude: parseInt(e.target.value) }))}
                            /> m
                        </div>

                        <div className="setting-row">
                            <label>분석 구간 수:</label>
                            <select
                                value={settings.segmentCount}
                                onChange={e => setSettings(s => ({ ...s, segmentCount: parseInt(e.target.value) }))}
                            >
                                <option value={3}>3 구간</option>
                                <option value={5}>5 구간</option>
                                <option value={10}>10 구간</option>
                            </select>
                        </div>

                        <div className="setting-row">
                            <label>드론 기종:</label>
                            <select
                                value={settings.droneType}
                                onChange={e => setSettings(s => ({ ...s, droneType: e.target.value }))}
                            >
                                {DRONE_TYPES.map(type => (
                                    <option key={type} value={type}>{type}</option>
                                ))}
                            </select>
                        </div>

                        <button
                            className="analyze-btn"
                            onClick={analyzeRoute}
                            disabled={loading || !pointA || !pointB}
                        >
                            {loading ? '⏳ 분석 중...' : '🚀 회랑 분석 시작'}
                        </button>
                    </div>

                    {/* 에러 */}
                    {error && (
                        <div className="error-message">❌ {error}</div>
                    )}

                    {/* 분석 결과 */}
                    {analysis && (
                        <>
                            {/* 종합 판정 */}
                            <div
                                className="overall-result"
                                style={{
                                    backgroundColor: getStatusColor(analysis.overall_judgment) + '20',
                                    borderColor: getStatusColor(analysis.overall_judgment)
                                }}
                            >
                                <h3>종합 판정</h3>
                                <div className="result-value" style={{ color: getStatusColor(analysis.overall_judgment) }}>
                                    {getStatusEmoji(analysis.overall_judgment)} {analysis.overall_judgment}
                                </div>
                                <div className="result-details">
                                    <span>📏 거리: {analysis.distance_m}m</span>
                                    <span>⏱️ 예상 비행 시간: {analysis.flight_time_min}분</span>
                                    <span>📍 권장 고도: {analysis.recommended_altitude}m</span>
                                </div>
                                <div className="result-details">
                                    <span>🌤️ 기상 체인: {formatSourceChain(analysis.weather_source_chain || analysis.weather_source)}</span>
                                    <span>🏙️ 건물 체인: {formatSourceChain(analysis.building_source_chain || analysis.source_chain)}</span>
                                </div>
                                {analysis.alternative_route && (
                                    <div className="alternative-notice">
                                        ⚠️ {analysis.alternative_route}
                                    </div>
                                )}
                            </div>

                            {/* 구간별 분석 */}
                            <div className="segments-panel">
                                <h3>📊 구간별 위험도</h3>
                                <div className="segments-grid">
                                    {analysis.segments.map(seg => (
                                        <div
                                            key={seg.id}
                                            className="segment-item"
                                            style={{ borderColor: getStatusColor(seg.status) }}
                                        >
                                            <div className="segment-header">
                                                <span>구간 {seg.id}</span>
                                                <span style={{ color: getStatusColor(seg.status) }}>
                                                    {getStatusEmoji(seg.status)} {seg.status}
                                                </span>
                                            </div>
                                            <div className="segment-info">
                                                <small>💨 풍속: {seg.wind_speed}m/s</small>
                                                <small>🏢 건물: {seg.building_height}m</small>
                                                <small>🔎 건물 신뢰도: {seg.building_confidence}</small>
                                                <small>🛰️ 건물 소스: {seg.building_source}</small>
                                            </div>
                                            <div className="segment-info">
                                                <small>🌤️ 기상 체인: {formatSourceChain(seg.weather_source_chain || seg.weather_source)}</small>
                                                <small>🏙️ 건물 체인: {formatSourceChain(seg.building_source_chain || seg.building_source)}</small>
                                            </div>
                                            <div className="segment-reason">{seg.reason}</div>
                                        </div>
                                    ))}
                                </div>
                            </div>

                            {/* 회랑 시각화 바 */}
                            <div className="corridor-bar">
                                <h3>🛤️ 회랑 시각화</h3>
                                <div className="bar-container">
                                    {analysis.segments.map(seg => (
                                        <div
                                            key={seg.id}
                                            className="bar-segment"
                                            style={{
                                                backgroundColor: getStatusColor(seg.status),
                                                width: `${100 / analysis.segments.length}%`
                                            }}
                                            title={`구간 ${seg.id}: ${seg.status}`}
                                        />
                                    ))}
                                </div>
                                <div className="bar-labels">
                                    <span>A 출발</span>
                                    <span>B 도착</span>
                                </div>
                            </div>
                        </>
                    )}
                </section>
            </div>
        </div>
    )
}

export default CorridorSimulation
