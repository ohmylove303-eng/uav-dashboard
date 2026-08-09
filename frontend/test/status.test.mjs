import test from 'node:test'
import assert from 'node:assert/strict'

import {
    getStatusColor,
    getStatusEmoji,
    getStatusLabel,
    getWorstStatus
} from '../src/status.js'

test('renders canonical API NO_GO as the red NO-GO display state', () => {
    assert.equal(getStatusLabel('NO_GO'), 'NO-GO')
    assert.equal(getStatusColor('NO_GO'), '#ef4444')
    assert.equal(getStatusEmoji('NO_GO'), '🔴')
    assert.equal(getWorstStatus(['GO', 'NO_GO', 'RESTRICT']), 'NO-GO')
})
