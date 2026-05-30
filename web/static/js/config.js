// Global Configuration Constants
export const TOPOLOGY_VERTICAL_OFFSET = 20;
export const BASE_PORT = 5000;

// Premium Color Palette matching CSS variables
export const COL = {
    accent: '#00f0ff',
    accentDim: '#4488ff',
    green: '#00ff88',
    amber: '#ffb300',
    red: '#ff3355',
    textMain: '#f1f5f9',
    textMuted: '#94a3b8',
    bgNodeCore: 'rgba(255, 51, 85, 0.1)',
    bgNodeAgg: 'rgba(0, 240, 255, 0.1)',
    bgNodeEdge: 'rgba(0, 255, 136, 0.1)',
    bgNodeHost: 'rgba(255, 255, 255, 0.05)',
    borderCore: '#ff3355',
    borderAgg: '#00f0ff',
    borderEdge: '#00ff88',
    borderHost: '#64748b',
    edgeIdle: 'rgba(255, 255, 255, 0.15)',
    edgeAggIdle: 'rgba(255, 255, 255, 0.25)',
    edgeCoreIdle: 'rgba(255, 255, 255, 0.35)',
};

// Map utilization ratio to status color
export function utilColor(u) {
    if (u < 0.3) return COL.green;
    if (u < 0.7) return COL.amber;
    return COL.red;
}
