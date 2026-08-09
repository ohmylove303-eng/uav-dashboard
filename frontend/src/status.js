const STATUS_WEIGHT = {
    GO: 0,
    RESTRICT: 1,
    'NO-GO': 2,
    NO_GO: 2
}

export function getStatusLabel(status) {
    return status === 'NO_GO' ? 'NO-GO' : status
}

export function getStatusColor(status) {
    switch (getStatusLabel(status)) {
        case 'GO': return '#22c55e'
        case 'RESTRICT': return '#eab308'
        case 'NO-GO': return '#ef4444'
        default: return '#64748b'
    }
}

export function getStatusEmoji(status) {
    switch (getStatusLabel(status)) {
        case 'GO': return '🟢'
        case 'RESTRICT': return '🟡'
        case 'NO-GO': return '🔴'
        default: return '⚪'
    }
}

export function getWorstStatus(statuses) {
    return statuses.map(getStatusLabel).reduce((worst, status) => (
        STATUS_WEIGHT[status] > STATUS_WEIGHT[worst] ? status : worst
    ), 'GO')
}
