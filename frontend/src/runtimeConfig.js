const RUNTIME_CONFIG_KEY = '__UAV_RUNTIME_CONFIG__'

function readRuntimeConfig() {
    if (typeof window === 'undefined') return {}

    const config = window[RUNTIME_CONFIG_KEY] || window.__UAV_RUNTIME_CONFIG__
    if (!config || typeof config !== 'object') {
        return {}
    }

    return config
}

export function getRuntimeConfigValue(...keys) {
    const config = readRuntimeConfig()

    for (const key of keys) {
        const value = config[key]
        if (typeof value === 'string' && value.trim()) {
            return value.trim()
        }
    }

    return ''
}
