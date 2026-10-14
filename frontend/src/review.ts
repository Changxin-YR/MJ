import type { Shot } from './types'

export function videoPassed(shot: Shot | null | undefined): boolean {
  const inspection = shot?.inspection_json
  return !!shot?.current_video_asset_id && inspection?.status === 'PASS'
    && (!inspection.kind || inspection.kind === 'VIDEO')
    && (!inspection.asset_id || inspection.asset_id === shot.current_video_asset_id)
}

export function videoReviewed(shot: Shot | null | undefined): boolean {
  const override = shot?.inspection_json?.review_override
  return videoPassed(shot) || !!(shot?.current_video_asset_id && override?.video_asset_id === shot.current_video_asset_id && override.reason.trim())
}
