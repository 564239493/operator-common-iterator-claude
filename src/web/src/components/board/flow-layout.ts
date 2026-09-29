/** Shared geometry: header cell boundaries and flow cell boundaries are identical. */
export const LABEL_WIDTH = 130
export const MIN_LANE_WIDTH = 154
export function laneWidth(viewport: number, count: number, zoom = 1): number {
  return Math.max(MIN_LANE_WIDTH, (viewport / Math.max(.1, zoom) - LABEL_WIDTH) / Math.max(1, count))
}
export function laneBoundary(index: number, width: number): number { return LABEL_WIDTH + index * width }
export function laneCenter(index: number, width: number): number { return laneBoundary(index, width) + width / 2 }
