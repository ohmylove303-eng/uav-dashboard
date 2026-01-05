import { MapContainer, TileLayer, Marker, useMapEvents } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import L from 'leaflet'

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

// 지도 클릭 이벤트 처리
function MapEvents({ onLocationSelect }) {
    useMapEvents({
        click(e) {
            onLocationSelect(e.latlng.lat, e.latlng.lng)
        },
    })
    return null
}

export default function MapView({ lat, lon, onLocationSelect }) {
    // OpenStreetMap (완전 무료, 키 불필요)

    return (
        <MapContainer
            center={[lat, lon]}
            zoom={15}
            style={{ height: '100%', width: '100%' }}
        >
            {/* OpenStreetMap 기본 지도 */}
            <TileLayer
                attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />

            <Marker position={[lat, lon]} />
            <MapEvents onLocationSelect={onLocationSelect} />
        </MapContainer>
    )
}
