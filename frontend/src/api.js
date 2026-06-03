import { getRuntimeConfigValue } from './runtimeConfig'

const STORAGE_KEY = 'uav_api_base_url'

function getImportMetaEnv() {
    return (typeof import.meta !== 'undefined' && import.meta.env) ? import.meta.env : {}
}

export function normalizeApiBaseUrl(value = '') {
    return value.trim().replace(/\/+$/, '')
}

export function getDefaultApiBaseUrl() {
    const env = getImportMetaEnv()
    return normalizeApiBaseUrl(
        getRuntimeConfigValue('VITE_API_URL', 'VITE_API_BASE_URL') ||
        env.VITE_API_URL ||
        env.VITE_API_BASE_URL ||
        ''
    )
}

export function getInitialApiBaseUrl() {
    if (typeof window === 'undefined') return getDefaultApiBaseUrl()

    const saved = window.localStorage.getItem(STORAGE_KEY)
    return saved === null ? getDefaultApiBaseUrl() : normalizeApiBaseUrl(saved)
}

export function saveApiBaseUrl(value) {
    const normalized = normalizeApiBaseUrl(value)

    if (normalized) {
        window.localStorage.setItem(STORAGE_KEY, normalized)
    } else {
        window.localStorage.removeItem(STORAGE_KEY)
    }

    return normalized
}

export function buildApiUrl(apiBaseUrl, path) {
    const base = normalizeApiBaseUrl(apiBaseUrl)
    const normalizedPath = path.startsWith('/') ? path : `/${path}`
    return `${base}${normalizedPath}`
}

export async function fetchJson(apiBaseUrl, path, options = {}) {
    const response = await fetch(buildApiUrl(apiBaseUrl, path), options)

    if (!response.ok) {
        let detail = ''
        try {
            detail = await response.text()
        } catch {
            detail = ''
        }

        throw new Error(detail || `HTTP ${response.status}`)
    }

    return response.json()
}
