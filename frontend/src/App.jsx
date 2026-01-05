import { useState, useEffect, useCallback } from 'react'
import MapView from './components/MapView'
import './App.css'

// API 기본 URL
const API_URL = 'http://localhost:8000'

function App() {
    // 상태 관리
    const [location, setLocation] = useState({ lat: 37.5665, lon: 126.9780 })
    const [weather, setWeather] = useState(null)
    const [evaluation, setEvaluation] = useState(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState(null)

    // 건물 높이 예측 상태
    const [buildingInfo, setBuildingInfo] = useState(null)
    const [buildingLoading, setBuildingLoading] = useState(false)

    // 입력 폼 상태
    const [formData, setFormData] = useState({
        building_height: 25,
        street_width: 15,
        wind_alignment: '직각',
        mission_altitude: 30,
        no_fly_zone: false,
        crowd_area: false,
        gps_locked: 12,
        glonass_locked: 6
    })

    // 지도 위치 선택 핸들러
    const handleLocationSelect = useCallback(async (lat, lon) => {
        setLocation({ lat, lon })

        // 1. 기상 정보 갱신
        fetchWeather(lat, lon)

        // 2. 건물 높이 예측
        setBuildingLoading(true)
        try {
            const res = await fetch(`${API_URL}/api/building-height?lat=${lat}&lon=${lon}`)
            const data = await res.json()

            setBuildingInfo(data)
            setFormData(prev => ({
                ...prev,
                building_height: data.estimated_height_m
            }))
        } catch (err) {
            console.error("Building height fetch error:", err)
        } finally {
            setBuildingLoading(false)
        }
    }, [])

    // 기상 정보 가져오기
    const fetchWeather = useCallback(async (lat, lon) => {
        if (!lat || !lon) {
            lat = location.lat
            lon = location.lon
        }
        try {
            const res = await fetch(`${API_URL}/api/weather?lat=${lat}&lon=${lon}`)
            const data = await res.json()
            setWeather(data.weather)
        } catch (err) {
            console.error('Weather fetch error:', err)
        }
    }, [location])

    // 판정 실행
    const performEvaluation = async () => {
        setLoading(true)
        setError(null)

        try {
            const res = await fetch(`${API_URL}/api/evaluate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    latitude: location.lat,
                    longitude: location.lon,
                    ...formData
                })
            })

            if (!res.ok) throw new Error('평가 실패')

            const data = await res.json()
            setEvaluation(data)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    // 초기 로드
    useEffect(() => {
        fetchWeather()
    }, [])

    // 게이트 상태 색상
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

    return (
        <div className="app">
            <header className="header">
                <h1>🚁 UAV 도시 운용판정 대시보드</h1>
                <p>4중 게이트 시스템 + 실시간 무료 데이터 (VWorld + Open-Meteo)</p>
            </header>

            <main className="main">
                {/* 좌측: 지도 + 입력 */}
                <section className="left-panel">
                    {/* 지도 영역 (VWorld) */}
                    <div className="map-container">
                        <div className="map-wrapper" style={{ height: '350px' }}>
                            <MapView
                                lat={location.lat}
                                lon={location.lon}
                                onLocationSelect={handleLocationSelect}
                            />
                        </div>

                        <div className="map-info" style={{ padding: '15px' }}>
                            <span>📍 선택 위치: {location.lat.toFixed(4)}, {location.lon.toFixed(4)}</span>
                            {buildingInfo && (
                                <span className="building-badge" style={{ marginLeft: '10px', background: '#3b82f6', padding: '3px 8px', borderRadius: '4px', fontSize: '0.9em' }}>
                                    🏢 {buildingInfo.zoning_type} (예측: {buildingInfo.estimated_floors}층)
                                </span>
                            )}
                        </div>
                    </div>

                    {/* 입력 폼 */}
                    <div className="input-form">
                        <h3>🏙️ 현장 정보</h3>

                        <div className="form-row">
                            <label>
                                건물 높이 (H):
                                <div className="input-with-hint" style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                                    <input
                                        type="number"
                                        value={formData.building_height}
                                        onChange={e => setFormData(prev => ({ ...prev, building_height: parseFloat(e.target.value) }))}
                                    />
                                    <span>m</span>
                                </div>
                                {buildingLoading && <small className="hint" style={{ color: '#eab308' }}>🔍 예측 중...</small>}
                            </label>
                            <label>
                                도로 폭 (W):
                                <div className="input-with-hint" style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                                    <input
                                        type="number"
                                        value={formData.street_width}
                                        onChange={e => setFormData(prev => ({ ...prev, street_width: parseFloat(e.target.value) }))}
                                    />
                                    <span>m</span>
                                </div>
                            </label>
                        </div>

                        <div className="form-row">
                            <label>
                                H/W 비율:
                                <strong>{(formData.building_height / formData.street_width).toFixed(2)}</strong>
                            </label>
                            <label>
                                풍향 정렬:
                                <select
                                    value={formData.wind_alignment}
                                    onChange={e => setFormData(prev => ({ ...prev, wind_alignment: e.target.value }))}
                                >
                                    <option value="일치">일치 (풍향=골목)</option>
                                    <option value="직각">직각</option>
                                    <option value="불명">불명</option>
                                </select>
                            </label>
                        </div>

                        <div className="form-row">
                            <label>
                                임무 고도:
                                <input
                                    type="number"
                                    value={formData.mission_altitude}
                                    onChange={e => setFormData(prev => ({ ...prev, mission_altitude: parseFloat(e.target.value) }))}
                                /> m
                            </label>
                            <label>
                                GPS 잠금:
                                <input
                                    type="number"
                                    value={formData.gps_locked}
                                    onChange={e => setFormData(prev => ({ ...prev, gps_locked: parseInt(e.target.value) }))}
                                /> 개
                            </label>
                        </div>

                        <h3>🚫 하드스탑 체크</h3>
                        <div className="form-row checkboxes">
                            <label className="checkbox-label">
                                <input
                                    type="checkbox"
                                    checked={formData.no_fly_zone}
                                    onChange={e => setFormData(prev => ({ ...prev, no_fly_zone: e.target.checked }))}
                                />
                                비행금지구역
                            </label>
                            <label className="checkbox-label">
                                <input
                                    type="checkbox"
                                    checked={formData.crowd_area}
                                    onChange={e => setFormData(prev => ({ ...prev, crowd_area: e.target.checked }))}
                                />
                                인파밀집지역
                            </label>
                        </div>

                        <button
                            onClick={performEvaluation}
                            className="btn btn-primary"
                            disabled={loading}
                        >
                            {loading ? '⏳ 판정 중...' : '🚀 비행 가능 여부 판정'}
                        </button>
                    </div>
                </section>

                {/* 우측: 결과 */}
                <section className="right-panel">
                    {/* 기상 정보 (기존과 동일) */}
                    <div className="weather-panel">
                        <h3>🌤️ 실시간 기상</h3>
                        {weather ? (
                            <div className="weather-grid">
                                <div className="weather-item">
                                    <span className="label">🌡️ 풍속</span>
                                    <span className="value">{weather.wind_speed?.toFixed(1)} m/s</span>
                                </div>
                                <div className="weather-item">
                                    <span className="label">💨 돌풍</span>
                                    <span className="value">{weather.gust_speed?.toFixed(1)} m/s</span>
                                </div>
                                <div className="weather-item">
                                    <span className="label">👁️ 시정</span>
                                    <span className="value">{weather.visibility?.toFixed(1)} km</span>
                                </div>
                                <div className="weather-item">
                                    <span className="label">🌧️ 강수</span>
                                    <span className="value">{weather.precipitation_prob}%</span>
                                </div>
                                <div className="weather-item">
                                    <span className="label">📡 Kp</span>
                                    <span className="value">{weather.kp_index}</span>
                                </div>
                                <div className="weather-item">
                                    <span className="label">🌡️ 기온</span>
                                    <span className="value">{weather.temperature}°C</span>
                                </div>
                            </div>
                        ) : (
                            <p>기상 정보 로딩 중...</p>
                        )}
                    </div>

                    {/* 게이트 상태 */}
                    {evaluation && (
                        <>
                            <div className="gates-panel">
                                <h3>🚦 게이트 상태</h3>
                                <div className="gates-grid">
                                    {evaluation.gates.map((gate, idx) => (
                                        <div
                                            key={idx}
                                            className="gate-item"
                                            style={{ borderColor: getStatusColor(gate.status) }}
                                        >
                                            <div className="gate-header">
                                                <span className="gate-name">{gate.gate}</span>
                                                <span className="gate-emoji">{getStatusEmoji(gate.status)}</span>
                                            </div>
                                            <div
                                                className="gate-status"
                                                style={{ color: getStatusColor(gate.status) }}
                                            >
                                                {gate.status}
                                            </div>
                                            <div className="gate-reason">{gate.reason}</div>
                                        </div>
                                    ))}
                                </div>
                            </div>

                            {/* EWS 정보 */}
                            <div className="ews-panel">
                                <h3>📊 도시 보정 결과</h3>
                                <div className="ews-info">
                                    <p><strong>EWS (Equivalent Wind Speed):</strong> {evaluation.ews} m/s</p>
                                    <p><strong>Fcanyon:</strong> {evaluation.urban_factors.Fcanyon}</p>
                                    <p><strong>H/W 비율:</strong> {evaluation.urban_factors.H_W_ratio}</p>
                                </div>
                            </div>

                            {/* 기종별 판정 */}
                            <div className="drone-panel">
                                <h3>🚁 기종별 판정</h3>
                                <div className="drone-grid">
                                    {Object.entries(evaluation.drone_judgments).map(([type, status]) => (
                                        <div
                                            key={type}
                                            className="drone-item"
                                            style={{ backgroundColor: getStatusColor(status) + '20', borderColor: getStatusColor(status) }}
                                        >
                                            <div className="drone-type">{type}</div>
                                            <div className="drone-status" style={{ color: getStatusColor(status) }}>
                                                {getStatusEmoji(status)} {status}
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>

                            {/* 층별풍 */}
                            <div className="layer-wind-panel">
                                <h3>📈 층별풍 (5m 간격)</h3>
                                <div className="layer-wind-grid">
                                    {Object.entries(evaluation.layer_winds).map(([alt, speed]) => (
                                        <div key={alt} className="layer-item">
                                            <span className="layer-alt">{alt}</span>
                                            <span className="layer-speed">{speed} m/s</span>
                                        </div>
                                    ))}
                                </div>
                            </div>

                            {/* 최종 판정 */}
                            <div
                                className="final-judgment"
                                style={{
                                    backgroundColor: getStatusColor(evaluation.final_judgment) + '30',
                                    borderColor: getStatusColor(evaluation.final_judgment)
                                }}
                            >
                                <h2>최종 판정</h2>
                                <div
                                    className="judgment-result"
                                    style={{ color: getStatusColor(evaluation.final_judgment) }}
                                >
                                    {getStatusEmoji(evaluation.final_judgment)} {evaluation.final_judgment}
                                </div>
                            </div>
                        </>
                    )}

                    {error && (
                        <div className="error-panel">
                            ❌ 오류: {error}
                        </div>
                    )}
                </section>
            </main>

            <footer className="footer">
                <p>📍 데이터: NOAA SWPC (Kp) | Open-Meteo (기상) | VWorld (지도)</p>
                <p>⚠️ 실제 비행 전 공식 채널(드론원스톱)에서 비행금지구역 확인 필수</p>
            </footer>
        </div>
    )
}

export default App
