import { catalogVersion, features, tourSteps } from './content'
export type Progress = { catalogVersion: string; tourId: string | null; stepIndex: number; completedLessonIds: string[] }
type StorageLike = Pick<Storage, 'getItem' | 'setItem'>
export const emptyProgress = (): Progress => ({ catalogVersion, tourId: null, stepIndex: 0, completedLessonIds: [] })
export function readProgress(storage: StorageLike | undefined, userId: string): Progress {
  try {
    const value = JSON.parse(storage?.getItem(`drishti-guide:${userId}`) ?? 'null')
    if (!value || value.catalogVersion !== catalogVersion || !Array.isArray(value.completedLessonIds)) return emptyProgress()
    const steps = typeof value.tourId === 'string' ? tourSteps(value.tourId) : []
    return { catalogVersion, tourId: steps.length ? value.tourId : null, stepIndex: Number.isInteger(value.stepIndex) && value.stepIndex >= 0 && value.stepIndex < steps.length ? value.stepIndex : 0, completedLessonIds: value.completedLessonIds.filter((id: unknown) => id === 'first-paper' || features.some(f => f.id === id)) }
  } catch { return emptyProgress() }
}
export function writeProgress(storage: StorageLike | undefined, userId: string, progress: Progress) {
  try { storage?.setItem(`drishti-guide:${userId}`, JSON.stringify(progress)) } catch { /* Continue in memory when storage is unavailable. */ }
}
