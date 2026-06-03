import { useCallback, useEffect, useMemo, useState } from 'react'
import { MapContainer, TileLayer, Marker, Polyline, Tooltip, useMap, useMapEvents } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import L from 'leaflet'
import VWorld3DMap from './VWorld3DMap'

// Leaflet 아이콘 문제 해결
import icon from 'leaflet/dist/images/marker-icon.png'
import iconShadow from 'leaflet/dist/images/marker-shadow.png'

let DefaultIcon = L.icon({
    iconUrl: icon,
    shadowUrl: iconShadow,
    iconSize: [25, 41],
    iconAnchor: [12, 41]
});

L.Marker.prototype.options.icon = DefaultIcon;

const createPointIcon = (label, color) => L.divIcon({
    className: 'route-marker',
    html: `<span style="--marker-color:${color}">${label}</span>`,
    iconSize: [34, 34],
    iconAnchor: [17, 17]
})

const pointAIcon = createPointIcon('A', '#22c55e')
const pointBIcon = createPointIcon('B', '#f97316')
const DEFAULT_ZOOM = 15

function leafletZoomToVWorldAltitude(zoom) {
    const altitude = 400000 / (2 ** (zoom - 6))
    return Math.round(Math.min(42000, Math.max(800, altitude)))
}

// 지도 클릭 이벤트 처리
function MapEvents({ onLocationSelect, onViewChange }) {
    useMapEvents({
        click(e) {
            onLocationSelect?.(e.latlng.lat, e.latlng.lng)
        },
        moveend(e) {
            const map = e.target
            const center = map.getCenter()
            onViewChange?.(center.lat, center.lng, map.getZoom())
        },
    })
    return null
}

function MapSync({ lat, lon, routePositions }) {
    const map = useMap()

    useEffect(() => {
        if (routePositions.length === 2) {
            map.fitBounds(routePositions, {
                padding: [44, 44],
                maxZoom: 16,
                animate: true
            })
            return
        }

        map.setView([lat, lon], map.getZoom(), { animate: true })
    }, [lat, lon, map, routePositions])

    return null
}

export default function MapView({
    lat,
    lon,
    onLocationSelect,
    pointA = null,
    pointB = null,
    showPrimaryMarker = true
}) {
    const [vworldStatus, setVworldStatus] = useState('loading')
    const [viewState, setViewState] = useState({ lat, lon, zoom: DEFAULT_ZOOM })
    const routePositions = useMemo(() => (
        [pointA, pointB]
            .filter(Boolean)
            .map(point => [point.lat, point.lon])
    ), [pointA, pointB])
    const vworldReady = vworldStatus === 'ready'
    const vworldAltitude = leafletZoomToVWorldAltitude(viewState.zoom)

    useEffect(() => {
        setViewState(current => ({ ...current, lat, lon }))
    }, [lat, lon])

    const handleViewChange = useCallback((nextLat, nextLon, nextZoom) => {
        setViewState(current => {
            const sameCenter =
                Math.abs(current.lat - nextLat) < 0.000001 &&
                Math.abs(current.lon - nextLon) < 0.000001
            const sameZoom = current.zoom === nextZoom

            if (sameCenter && sameZoom) {
                return current
            }

            return { lat: nextLat, lon: nextLon, zoom: nextZoom }
        })
    }, [])

    const handleVWorldStatusChange = useCallback(status => {
        setVworldStatus(status)
    }, [])

    return (
        <div className={`uav-map-shell ${vworldReady ? 'has-vworld-3d' : ''}`}>
            <VWorld3DMap
                lat={viewState.lat}
                lon={viewState.lon}
                altitude={vworldAltitude}
                onStatusChange={handleVWorldStatusChange}
            />

            <MapContainer
                center={[lat, lon]}
                zoom={DEFAULT_ZOOM}
                className={`leaflet-overlay-map ${vworldReady ? 'is-3d-base' : ''}`}
                style={{ height: '100%', width: '100%' }}
            >
                <MapSync lat={lat} lon={lon} routePositions={routePositions} />

                {!vworldReady && (
                    <TileLayer
                        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                    />
                )}

                {showPrimaryMarker && <Marker position={[lat, lon]} />}
                {pointA && (
                    <Marker position={[pointA.lat, pointA.lon]} icon={pointAIcon}>
                        <Tooltip direction="top" offset={[0, -16]}>출발점 A</Tooltip>
                    </Marker>
                )}
                {pointB && (
                    <Marker position={[pointB.lat, pointB.lon]} icon={pointBIcon}>
                        <Tooltip direction="top" offset={[0, -16]}>도착점 B</Tooltip>
                    </Marker>
                )}
                {routePositions.length === 2 && (
                    <Polyline
                        positions={routePositions}
                        pathOptions={{ color: '#38bdf8', weight: 5, opacity: 0.85, dashArray: '8 8' }}
                    />
                )}
                <MapEvents onLocationSelect={onLocationSelect} onViewChange={handleViewChange} />
            </MapContainer>
        </div>
    )
}
