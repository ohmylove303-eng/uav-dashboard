import { useState, useEffect, useCallback } from 'react'
import MapView from './components/MapView'
import CorridorSimulation from './components/CorridorSimulation'
import {
    fetchJson,
    getDefaultApiBaseUrl,
    getInitialApiBaseUrl,
    normalizeApiBaseUrl,
    saveApiBaseUrl
} from './api'
import './App.css'

const TABS = [
    { id: 'flight', label: '🚦 비행 판정', description: '4중 게이트 시스템' },
    { id: 'corridor', label: '🛤️ 회랑 시뮬레이션', description: '경로 위험도 분석', isNew: true }
]

const DRONE_SPECS = {
    'DJI Mini 3 Pro': { wind: 10.7, gust: 13.0, desc: '소형' },
    'DJI Mavic 3': { wind: 12.0, gust: 15.0, desc: '준전문가용' },
    'DJI Matrice 300 RTK': { wind: 15.0, gust: 18.0, desc: '산업용' },
    'DJI Inspire 3': { wind: 14.0, gust: 16.0, desc: '전문 촬영용' },
    'Custom (Generic)': { wind: 10.0, gust: 12.0, desc: '일반 기준' }
}

const DRONE_MODELS = Object.keys(DRONE_SPECS)

const STATUS_WEIGHT = {
    GO: 0,
    RESTRICT: 1,
    'NO-GO': 2
}

function toNumber(value, fallback = 0) {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : fallback
}

function formatNumber(value, digits = 1, fallback = '-') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed.toFixed(digits) : fallback
}

function getStatusColor(status) {
    switch (status) {
        case 'GO': return '#22c55e'
        case 'RESTRICT': return '#eab308'
        case 'NO-GO': return '#ef4444'
        default: return '#64748b'
    }
}

function getStatusEmoji(status) {
    switch (status) {
        case 'GO': return '🟢'
        case 'RESTRICT': return '🟡'
        case 'NO-GO': return '🔴'
        default: return '⚪'
    }
}

function getWorstStatus(statuses) {
    return statuses.reduce((worst, status) => (
        STATUS_WEIGHT[status] > STATUS_WEIGHT[worst] ? status : worst
    ), 'GO')
}

function createFallbackWeather() {
    return {
        wind_speed: 5.0,
        gust_speed: 8.0,
        wind_direction: 0,
        visibility: 10.0,
        precipitation_prob: 10,
        weather_code: 0,
        temperature: 20,
        humidity: 50,
        cloud_cover: 20,
        kp_index: 3.0,
        source: 'browser_fallback',
        stale_cache: false
    }
}

function buildClientEvaluation(location, formData, weatherInput) {
    const weather = {
        ...createFallbackWeather(),
        ...(weatherInput || {}),
        source: weatherInput?.source || 'browser_fallback'
    }

    const spec = DRONE_SPECS[formData.drone_model] || DRONE_SPECS['DJI Mavic 3']
    const buildingHeight = Math.max(0, toNumber(formData.building_height, 25))
    const streetWidth = Math.max(1, toNumber(formData.street_width, 15))
    const missionAltitude = Math.max(5, toNumber(formData.mission_altitude, 30))
    const hwRatio = buildingHeight / streetWidth
    const fcanyon = 1 + 0.3 * Math.min(hwRatio, 3)
    const alignFactor = { 일치: 1.3, 직각: 0.9, 불명: 1.1 }[formData.wind_alignment] || 1
    const windSpeed = Math.max(0, toNumber(weather.wind_speed, 5))
    const gustSpeed = Math.max(0, toNumber(weather.gust_speed, 8))
    const visibility = Math.max(0, toNumber(weather.visibility, 10))
    const rainProb = Math.max(0, toNumber(weather.precipitation_prob, 10))
    const ews = windSpeed * fcanyon * 1.2 * 1.3 * alignFactor
    const effectiveGust = gustSpeed * 1.3
    const gate0Reasons = []

    if (formData.no_fly_zone) gate0Reasons.push('비행금지구역')
    if (formData.crowd_area) gate0Reasons.push('인파밀집지역')
    if (rainProb >= 70) gate0Reasons.push(`강수확률 높음(${Math.round(rainProb)}%)`)
    if (toNumber(weather.weather_code, 0) >= 51) gate0Reasons.push('현재 비/눈')

    const gate0Status = gate0Reasons.length ? 'NO-GO' : 'GO'
    const gpsCount = toNumber(formData.gps_locked, 0)
    const glonassCount = toNumber(formData.glonass_locked, 0)
    const gate1Status = gpsCount >= 8 && glonassCount >= 4 ? 'GO' : 'NO-GO'
    const gate2Status = visibility >= 3 ? 'GO' : visibility < 1 ? 'NO-GO' : 'RESTRICT'
    const gate3Status = ews <= spec.wind * 0.8 ? 'GO' : ews <= spec.wind ? 'RESTRICT' : 'NO-GO'
    const gate4Status = effectiveGust <= spec.gust ? 'GO' : 'NO-GO'

    const gates = [
        {
            gate: 'Gate0',
            status: gate0Status,
            reason: gate0Reasons.length ? gate0Reasons.join(' / ') : '비행 방해 요소 없음'
        },
        {
            gate: 'Gate1',
            status: gate1Status,
            reason: gate1Status === 'GO'
                ? `양호 (GPS:${gpsCount}, GLO:${glonassCount})`
                : `위성신호 부족 (GPS:${gpsCount}, GLO:${glonassCount})`,
            threshold: 'GPS 8+, GLO 4+'
        },
        {
            gate: 'Gate2',
            status: gate2Status,
            reason: gate2Status === 'GO'
                ? `시정 양호 (${formatNumber(visibility)}km)`
                : gate2Status === 'RESTRICT'
                    ? `안개 주의 (${formatNumber(visibility)}km)`
                    : `시야 미확보 (${formatNumber(visibility)}km)`,
            threshold: '3km'
        },
        {
            gate: 'Gate3',
            status: gate3Status,
            reason: `빌딩풍 ${formatNumber(ews)}m/s vs 한계 ${spec.wind}m/s`,
            value: Number(ews.toFixed(2)),
            threshold: String(spec.wind)
        },
        {
            gate: 'Gate4',
            status: gate4Status,
            reason: `순간돌풍 ${formatNumber(effectiveGust)}m/s vs 한계 ${spec.gust}m/s`,
            value: Number(effectiveGust.toFixed(2)),
            threshold: String(spec.gust)
        }
    ]

    const maxLayerAltitude = Math.max(50, Math.min(200, Math.ceil(missionAltitude / 5) * 5))
    const profileLayers = Array.from({ length: Math.floor(maxLayerAltitude / 5) + 1 }, (_, index) => {
        const altitude = index * 5
        const layerWind = windSpeed * (1 + Math.min(altitude, 200) * 0.003)
        const temperature = toNumber(weather.temperature, 20) - 0.0065 * altitude

        return {
            height_m: altitude,
            wind_speed_mps: Number(layerWind.toFixed(2)),
            wind_direction_deg: toNumber(weather.wind_direction, 0),
            temperature_c: Number(temperature.toFixed(1)),
            density: Number((1.225 * Math.exp(-altitude / 8434.5)).toFixed(3))
        }
    })

    return {
        timestamp: new Date().toISOString(),
        location: { lat: location.lat, lon: location.lon },
        weather,
        urban_factors: {
            H: buildingHeight,
            W: streetWidth,
            H_W_ratio: Number(hwRatio.toFixed(2)),
            Fcanyon: Number(fcanyon.toFixed(2)),
            alignment_factor: alignFactor,
            mission_altitude: missionAltitude
        },
        gates,
        final_judgment: getWorstStatus(gates.map(gate => gate.status)),
        ews: Number(ews.toFixed(2)),
        drone_spec: spec,
        source: 'client_fallback',
        profile_source: 'browser_synthetic',
        selected_layer: profileLayers.find(layer => layer.height_m === Math.round(missionAltitude / 5) * 5) || profileLayers[0],
        profile_layers: profileLayers,
        offline: true
    }
}

function getLayerWindItems(evaluation) {
    if (!evaluation) return []

    if (Array.isArray(evaluation.profile_layers) && evaluation.profile_layers.length) {
        return evaluation.profile_layers.map(layer => ({
            altitude: `${formatNumber(layer.height_m, 0)}m`,
            speed: `${formatNumber(layer.wind_speed_mps)} m/s`,
            density: layer.density ? `ρ ${formatNumber(layer.density, 3)}` : null
        }))
    }

    if (evaluation.layer_winds) {
        return Object.entries(evaluation.layer_winds).map(([altitude, speed]) => ({
            altitude,
            speed: `${formatNumber(speed)} m/s`,
            density: null
        }))
    }

    if (evaluation.selected_layer) {
        return [{
            altitude: `${formatNumber(evaluation.selected_layer.height_m, 0)}m`,
            speed: `${formatNumber(evaluation.selected_layer.wind_speed_mps)} m/s`,
            density: evaluation.selected_layer.density ? `ρ ${formatNumber(evaluation.selected_layer.density, 3)}` : null
        }]
    }

    return []
}

function App() {
    const [activeTab, setActiveTab] = useState('flight')
    const [showBanner, setShowBanner] = useState(true)
    const [apiBaseUrl, setApiBaseUrl] = useState(getInitialApiBaseUrl)
    const [apiBaseDraft, setApiBaseDraft] = useState(apiBaseUrl)
    const [apiHealth, setApiHealth] = useState({
        status: 'checking',
        message: '백엔드 연결 확인 중'
    })

    const [location, setLocation] = useState({ lat: 37.5665, lon: 126.9780 })
    const [weather, setWeather] = useState(null)
    const [evaluation, setEvaluation] = useState(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState(null)
    const [buildingInfo, setBuildingInfo] = useState(null)
    const [buildingLoading, setBuildingLoading] = useState(false)

    const [formData, setFormData] = useState({
        building_height: 25,
        street_width: 15,
        wind_alignment: '직각',
        mission_altitude: 30,
        no_fly_zone: false,
        crowd_area: false,
        gps_locked: 12,
        glonass_locked: 6,
        drone_model: 'DJI Mavic 3'
    })

    const setNumberField = (field, value) => {
        setFormData(prev => ({
            ...prev,
            [field]: value === '' ? '' : Number(value)
        }))
    }

    const checkApiHealth = useCallback(async (baseUrl) => {
        setApiHealth(prev => ({
            status: prev.status === 'online' ? 'checking' : prev.status,
            message: '백엔드 연결 확인 중'
        }))

        try {
            await fetchJson(baseUrl, '/api/kp')
            setApiHealth({
                status: 'online',
                message: '실시간 API 연결됨'
            })
        } catch {
            setApiHealth({
                status: 'offline',
                message: 'API 연결 실패. 임시 데이터로 표시 중'
            })
        }
    }, [])

    const fetchWeather = useCallback(async (latArg, lonArg, baseUrl = apiBaseUrl) => {
        const targetLat = latArg ?? location.lat
        const targetLon = lonArg ?? location.lon

        try {
            const data = await fetchJson(
                baseUrl,
                `/api/weather?lat=${encodeURIComponent(targetLat)}&lon=${encodeURIComponent(targetLon)}`
            )
            setWeather(data.weather)
            setApiHealth({
                status: 'online',
                message: '실시간 API 연결됨'
            })
        } catch {
            setWeather(createFallbackWeather())
            setApiHealth({
                status: 'offline',
                message: '기상 API 연결 실패. 임시값 표시 중'
            })
        }
    }, [apiBaseUrl, location.lat, location.lon])

    const handleLocationSelect = useCallback(async (lat, lon) => {
        setLocation({ lat, lon })
        fetchWeather(lat, lon)
        setBuildingLoading(true)

        try {
            const data = await fetchJson(
                apiBaseUrl,
                `/api/building-height?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`
            )

            setBuildingInfo(data)
            if (Number.isFinite(Number(data.estimated_height_m))) {
                setFormData(prev => ({
                    ...prev,
                    building_height: data.estimated_height_m
                }))
            }
        } catch {
            setBuildingInfo(null)
        } finally {
            setBuildingLoading(false)
        }
    }, [apiBaseUrl, fetchWeather])

    const performEvaluation = async () => {
        setLoading(true)
        setError(null)

        try {
            const data = await fetchJson(apiBaseUrl, '/api/evaluate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    latitude: location.lat,
                    longitude: location.lon,
                    ...formData
                })
            })

            setEvaluation(data)
            setApiHealth({
                status: 'online',
                message: '실시간 API 연결됨'
            })
        } catch (err) {
            const fallbackEvaluation = buildClientEvaluation(location, formData, weather)
            setEvaluation(fallbackEvaluation)
            setError(null)
            setApiHealth({
                status: 'offline',
                message: '판정 API 연결 실패. 브라우저 임시 판정 표시 중'
            })
        } finally {
            setLoading(false)
        }
    }

    const handleApiSubmit = (event) => {
        event.preventDefault()
        const normalized = saveApiBaseUrl(apiBaseDraft)
        setApiBaseUrl(normalized)
        setApiBaseDraft(normalized)
        setEvaluation(null)
        setError(null)
    }

    const handleApiReset = () => {
        saveApiBaseUrl('')
        const defaultBase = getDefaultApiBaseUrl()
        setApiBaseUrl(defaultBase)
        setApiBaseDraft(defaultBase)
        setEvaluation(null)
        setError(null)
    }

    useEffect(() => {
        setApiBaseDraft(apiBaseUrl)
        checkApiHealth(apiBaseUrl)
        fetchWeather(location.lat, location.lon, apiBaseUrl)
    }, [apiBaseUrl])

    const apiLabel = {
        checking: '확인 중',
        online: '연결됨',
        offline: '오프라인'
    }[apiHealth.status]

    const apiBaseDisplay = normalizeApiBaseUrl(apiBaseUrl) || '/api'
    const hValue = toNumber(formData.building_height, 0)
    const wValue = Math.max(1, toNumber(formData.street_width, 1))
    const hwRatio = hValue / wValue
    const layerWindItems = getLayerWindItems(evaluation)
    const droneJudgments = evaluation?.drone_judgments
        ? Object.entries(evaluation.drone_judgments)
        : evaluation
            ? [[formData.drone_model, evaluation.final_judgment]]
            : []
    const selectedSpec = evaluation?.drone_spec || DRONE_SPECS[formData.drone_model]
    const urbanFactors = evaluation?.urban_factors || {}
    const evaluationHwRatio = urbanFactors.H_W_ratio ?? (
        toNumber(urbanFactors.W, 0) > 0 ? toNumber(urbanFactors.H, 0) / toNumber(urbanFactors.W, 1) : null
    )

    return (
        <div className="app">
            <header className="header">
                <h1>🚁 UAV 도시 운용판정 대시보드</h1>
                <p>도시 협곡 보정, 상층풍, 실시간 기상을 함께 보는 비행 판단 콘솔</p>
            </header>

            {showBanner && (
                <div className="feature-banner">
                    <span className="banner-icon">🛤️</span>
                    <span className="banner-text">
                        <strong>NEW</strong> 회랑 시뮬레이션에서 A→B 경로와 구간 위험도를 함께 확인할 수 있습니다.
                    </span>
                    <button
                        className="banner-close"
                        onClick={() => setShowBanner(false)}
                        aria-label="배너 닫기"
                    >
                        ×
                    </button>
                </div>
            )}

            <nav className="tab-navigation">
                {TABS.map(tab => (
                    <button
                        key={tab.id}
                        className={`tab-btn ${activeTab === tab.id ? 'active' : ''}`}
                        onClick={() => setActiveTab(tab.id)}
                    >
                        <span className="tab-label">{tab.label}</span>
                        {tab.isNew && <span className="tab-new-badge">NEW</span>}
                        <span className="tab-desc">{tab.description}</span>
                    </button>
                ))}
            </nav>

            <section className={`api-status-bar ${apiHealth.status}`} aria-live="polite">
                <div className="api-status-main">
                    <span className="api-status-dot" />
                    <div>
                        <strong>API {apiLabel}</strong>
                        <span>{apiHealth.message} · {apiBaseDisplay}</span>
                    </div>
                </div>
                <form className="api-url-form" onSubmit={handleApiSubmit}>
                    <input
                        value={apiBaseDraft}
                        onChange={event => setApiBaseDraft(event.target.value)}
                        placeholder="백엔드 주소"
                        aria-label="API 기본 주소"
                    />
                    <button type="submit">저장</button>
                    <button type="button" onClick={handleApiReset}>초기화</button>
                </form>
            </section>

            {activeTab === 'flight' && (
                <main className="main">
                    <section className="left-panel">
                        <div className="map-container">
                            <div className="map-wrapper">
                                <MapView
                                    lat={location.lat}
                                    lon={location.lon}
                                    onLocationSelect={handleLocationSelect}
                                />
                            </div>

                            <div className="map-info">
                                <span>📍 {location.lat.toFixed(4)}, {location.lon.toFixed(4)}</span>
                                {buildingInfo && (
                                    <span className="building-badge">
                                        🏢 {buildingInfo.zoning_type} · {buildingInfo.estimated_floors}층 예측
                                    </span>
                                )}
                            </div>
                        </div>

                        <div className="input-form">
                            <h3>🏙️ 현장 정보</h3>

                            <div className="form-row">
                                <label>
                                    건물 높이 (H)
                                    <div className="input-with-hint">
                                        <input
                                            type="number"
                                            min="0"
                                            value={formData.building_height}
                                            onChange={event => setNumberField('building_height', event.target.value)}
                                        />
                                        <span>m</span>
                                    </div>
                                    {buildingLoading && <small className="hint">예측 중...</small>}
                                </label>
                                <label>
                                    도로 폭 (W)
                                    <div className="input-with-hint">
                                        <input
                                            type="number"
                                            min="1"
                                            value={formData.street_width}
                                            onChange={event => setNumberField('street_width', event.target.value)}
                                        />
                                        <span>m</span>
                                    </div>
                                </label>
                            </div>

                            <div className="form-row">
                                <label>
                                    H/W 비율
                                    <strong>{formatNumber(hwRatio, 2)}</strong>
                                </label>
                                <label>
                                    풍향 정렬
                                    <select
                                        value={formData.wind_alignment}
                                        onChange={event => setFormData(prev => ({ ...prev, wind_alignment: event.target.value }))}
                                    >
                                        <option value="일치">일치 (풍향=골목)</option>
                                        <option value="직각">직각</option>
                                        <option value="불명">불명</option>
                                    </select>
                                </label>
                            </div>

                            <div className="form-row">
                                <label>
                                    임무 고도
                                    <div className="input-with-hint">
                                        <input
                                            type="number"
                                            min="5"
                                            value={formData.mission_altitude}
                                            onChange={event => setNumberField('mission_altitude', event.target.value)}
                                        />
                                        <span>m</span>
                                    </div>
                                </label>
                                <label>
                                    드론 기종
                                    <select
                                        value={formData.drone_model}
                                        onChange={event => setFormData(prev => ({ ...prev, drone_model: event.target.value }))}
                                    >
                                        {DRONE_MODELS.map(model => (
                                            <option key={model} value={model}>{model}</option>
                                        ))}
                                    </select>
                                </label>
                            </div>

                            <div className="form-row">
                                <label>
                                    GPS 잠금
                                    <div className="input-with-hint">
                                        <input
                                            type="number"
                                            min="0"
                                            value={formData.gps_locked}
                                            onChange={event => setNumberField('gps_locked', event.target.value)}
                                        />
                                        <span>개</span>
                                    </div>
                                </label>
                                <label>
                                    GLONASS 잠금
                                    <div className="input-with-hint">
                                        <input
                                            type="number"
                                            min="0"
                                            value={formData.glonass_locked}
                                            onChange={event => setNumberField('glonass_locked', event.target.value)}
                                        />
                                        <span>개</span>
                                    </div>
                                </label>
                            </div>

                            <h3>🚫 하드스탑 체크</h3>
                            <div className="form-row checkboxes">
                                <label className="checkbox-label">
                                    <input
                                        type="checkbox"
                                        checked={formData.no_fly_zone}
                                        onChange={event => setFormData(prev => ({ ...prev, no_fly_zone: event.target.checked }))}
                                    />
                                    비행금지구역
                                </label>
                                <label className="checkbox-label">
                                    <input
                                        type="checkbox"
                                        checked={formData.crowd_area}
                                        onChange={event => setFormData(prev => ({ ...prev, crowd_area: event.target.checked }))}
                                    />
                                    인파밀집지역
                                </label>
                            </div>

                            <button
                                onClick={performEvaluation}
                                className="btn btn-primary"
                                disabled={loading}
                            >
                                {loading ? '판정 중...' : '비행 가능 여부 판정'}
                            </button>
                        </div>
                    </section>

                    <section className="right-panel">
                        <div className="weather-panel">
                            <div className="panel-heading">
                                <h3>🌤️ 실시간 기상</h3>
                                {weather?.source && <span>{weather.source}</span>}
                            </div>
                            {weather ? (
                                <div className="weather-grid">
                                    <div className="weather-item">
                                        <span className="label">풍속</span>
                                        <span className="value">{formatNumber(weather.wind_speed)} m/s</span>
                                    </div>
                                    <div className="weather-item">
                                        <span className="label">돌풍</span>
                                        <span className="value">{formatNumber(weather.gust_speed)} m/s</span>
                                    </div>
                                    <div className="weather-item">
                                        <span className="label">시정</span>
                                        <span className="value">{formatNumber(weather.visibility)} km</span>
                                    </div>
                                    <div className="weather-item">
                                        <span className="label">강수</span>
                                        <span className="value">{formatNumber(weather.precipitation_prob, 0)}%</span>
                                    </div>
                                    <div className="weather-item">
                                        <span className="label">Kp</span>
                                        <span className="value">{formatNumber(weather.kp_index, 1)}</span>
                                    </div>
                                    <div className="weather-item">
                                        <span className="label">기온</span>
                                        <span className="value">{formatNumber(weather.temperature, 0)}°C</span>
                                    </div>
                                </div>
                            ) : (
                                <p className="empty-state">기상 정보 로딩 중...</p>
                            )}
                        </div>

                        {evaluation && (
                            <>
                                {evaluation.offline && (
                                    <div className="offline-panel">
                                        API 미연결 상태의 임시 판정입니다. 실제 비행 전 공식 데이터로 재확인하세요.
                                    </div>
                                )}

                                <div className="gates-panel">
                                    <h3>🚦 게이트 상태</h3>
                                    <div className="gates-grid">
                                        {(evaluation.gates || []).map((gate, idx) => (
                                            <div
                                                key={`${gate.gate}-${idx}`}
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

                                <div className="ews-panel">
                                    <h3>📊 도시 보정 결과</h3>
                                    <div className="ews-info">
                                        <p><strong>EWS:</strong> {formatNumber(evaluation.ews)} m/s</p>
                                        <p><strong>Fcanyon:</strong> {formatNumber(urbanFactors.Fcanyon, 2)}</p>
                                        <p><strong>H/W 비율:</strong> {formatNumber(evaluationHwRatio, 2)}</p>
                                        <p><strong>프로파일:</strong> {evaluation.profile_source || 'surface_only'}</p>
                                    </div>
                                </div>

                                <div className="drone-panel">
                                    <h3>🚁 기종 판정</h3>
                                    <div className="drone-grid">
                                        {droneJudgments.map(([type, status]) => (
                                            <div
                                                key={type}
                                                className="drone-item"
                                                style={{
                                                    backgroundColor: `${getStatusColor(status)}22`,
                                                    borderColor: getStatusColor(status)
                                                }}
                                            >
                                                <div className="drone-type">{type}</div>
                                                <div className="drone-status" style={{ color: getStatusColor(status) }}>
                                                    {getStatusEmoji(status)} {status}
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                    {selectedSpec && (
                                        <div className="spec-strip">
                                            <span>{selectedSpec.desc}</span>
                                            <span>지속풍 {formatNumber(selectedSpec.wind)} m/s</span>
                                            <span>돌풍 {formatNumber(selectedSpec.gust)} m/s</span>
                                        </div>
                                    )}
                                </div>

                                <div className="layer-wind-panel">
                                    <h3>📈 층별풍</h3>
                                    {layerWindItems.length ? (
                                        <div className="layer-wind-grid">
                                            {layerWindItems.map((layer, idx) => (
                                                <div key={`${layer.altitude}-${idx}`} className="layer-item">
                                                    <span className="layer-alt">{layer.altitude}</span>
                                                    <span className="layer-speed">{layer.speed}</span>
                                                    {layer.density && <span className="layer-density">{layer.density}</span>}
                                                </div>
                                            ))}
                                        </div>
                                    ) : (
                                        <p className="empty-state">층별풍 데이터 없음</p>
                                    )}
                                </div>

                                <div
                                    className="final-judgment"
                                    style={{
                                        backgroundColor: `${getStatusColor(evaluation.final_judgment)}24`,
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
                                {error}
                            </div>
                        )}
                    </section>
                </main>
            )}

            {activeTab === 'corridor' && (
                <CorridorSimulation apiBaseUrl={apiBaseUrl} />
            )}

            <footer className="footer">
                <p>데이터: NOAA SWPC (Kp) | Open-Meteo (기상) | KMA 상층풍 | VWorld 건물정보</p>
                <p>실제 비행 전 드론원스톱에서 비행금지구역 확인 필수</p>
            </footer>
        </div>
    )
}

export default App
