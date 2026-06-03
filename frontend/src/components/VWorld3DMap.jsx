import { useEffect, useMemo, useRef, useState } from 'react'
import { getRuntimeConfigValue } from '../runtimeConfig'

const SCRIPT_ID = 'vworld-3d-webgl-script'
const DEFAULT_ALTITUDE = 1800

let scriptPromise = null

function getVWorldApiKey() {
    const env = import.meta.env || {}
    return (
        getRuntimeConfigValue(
            'VITE_VWORLD_API_KEY',
            'VITE_VWORLD_3D_API_KEY',
            'VITE_VWORLD_KEY',
            'VWORLD_API_KEY'
        ) ||
        env.VITE_VWORLD_3D_API_KEY ||
        env.VITE_VWORLD_API_KEY ||
        env.VITE_VWORLD_KEY ||
        ''
    ).trim()
}

function buildVWorld3DScriptUrl(apiKey) {
    const params = new URLSearchParams({
        version: '3.0',
        apiKey
    })

    return `https://map.vworld.kr/js/webglMapInit.js.do?${params.toString()}`
}

function loadVWorld3DScript() {
    const apiKey = getVWorldApiKey()

    if (!apiKey) {
        return Promise.reject(new Error('missing_vworld_api_key'))
    }

    if (window.vw?.Map) {
        return Promise.resolve()
    }

    if (scriptPromise) {
        return scriptPromise
    }

    scriptPromise = new Promise((resolve, reject) => {
        const existingScript = document.getElementById(SCRIPT_ID)

        if (existingScript) {
            if (existingScript.dataset.loaded === 'true') {
                resolve()
                return
            }

            existingScript.addEventListener('load', () => resolve(), { once: true })
            existingScript.addEventListener('error', () => reject(new Error('vworld_script_error')), { once: true })
            return
        }

        const script = document.createElement('script')
        script.id = SCRIPT_ID
        script.src = buildVWorld3DScriptUrl(apiKey)
        script.async = true
        script.onload = () => {
            script.dataset.loaded = 'true'
            resolve()
        }
        script.onerror = () => reject(new Error('vworld_script_error'))

        document.head.appendChild(script)
    }).catch(error => {
        scriptPromise = null
        throw error
    })

    return scriptPromise
}

function makeCameraPosition(vw, lat, lon, altitude) {
    const cameraAltitude = Number.isFinite(altitude) ? altitude : DEFAULT_ALTITUDE
    return new vw.CameraPosition(
        new vw.CoordZ(lon, lat, cameraAltitude),
        new vw.Direction(0, -65, 0)
    )
}

function createVWorldMap({ mapId, lat, lon, altitude }) {
    const vw = window.vw
    const map = new vw.Map()
    const initPosition = makeCameraPosition(vw, lat, lon, altitude)

    if (typeof map.setOption === 'function') {
        map.setOption({
            mapId,
            initPosition,
            logo: false,
            navigation: true
        })
    }

    if (typeof map.setMapId === 'function') {
        map.setMapId(mapId)
    }

    if (typeof map.setInitPosition === 'function') {
        map.setInitPosition(initPosition)
    }

    if (typeof map.setLogoVisible === 'function') {
        map.setLogoVisible(false)
    }

    if (typeof map.setNavigationZoomVisible === 'function') {
        map.setNavigationZoomVisible(false)
    }

    if (typeof map.start === 'function') {
        map.start()
    }

    return map
}

function disposeMap(map, container) {
    if (!map) return

    for (const method of ['destroy', 'remove', 'dispose']) {
        if (typeof map[method] === 'function') {
            map[method]()
            break
        }
    }

    if (container) {
        container.innerHTML = ''
    }
}

export default function VWorld3DMap({ lat, lon, altitude = DEFAULT_ALTITUDE, onStatusChange }) {
    const containerRef = useRef(null)
    const mapRef = useRef(null)
    const [message, setMessage] = useState('VWorld 3D 불러오는 중')
    const mapId = useMemo(
        () => `vworld-3d-${Math.random().toString(36).slice(2, 10)}`,
        []
    )

    useEffect(() => {
        let cancelled = false
        const alreadyStarted = Boolean(mapRef.current)

        if (!alreadyStarted) {
            setMessage('VWorld 3D 불러오는 중')
            onStatusChange?.('loading')
        }

        loadVWorld3DScript()
            .then(() => {
                if (cancelled || !containerRef.current) return

                const vw = window.vw
                if (!vw?.Map || !vw?.CameraPosition || !vw?.CoordZ || !vw?.Direction) {
                    throw new Error('vworld_api_not_ready')
                }

                disposeMap(mapRef.current, containerRef.current)
                mapRef.current = createVWorldMap({ mapId, lat, lon, altitude })

                if (!cancelled) {
                    setMessage('VWorld 3D 기본 지도')
                    onStatusChange?.('ready')
                }
            })
            .catch(error => {
                if (cancelled) return

                disposeMap(mapRef.current, containerRef.current)
                mapRef.current = null

                const missingKey = error?.message === 'missing_vworld_api_key'
                setMessage(missingKey ? 'VWorld 3D API 키 필요' : 'VWorld 3D 로드 실패 - 2D 지도 대체')
                onStatusChange?.(missingKey ? 'missing-key' : 'error')
            })

        return () => {
            cancelled = true
        }
    }, [altitude, lat, lon, mapId, onStatusChange])

    useEffect(() => () => {
        disposeMap(mapRef.current, containerRef.current)
        mapRef.current = null
    }, [])

    return (
        <>
            <div className="vworld-3d-base" aria-hidden="true">
                <div id={mapId} ref={containerRef} className="vworld-3d-canvas" />
            </div>
            <div className="vworld-3d-badge">{message}</div>
        </>
    )
}
