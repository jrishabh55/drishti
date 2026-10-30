// Narration bus level. The engine normalises each take to just under full scale
// (measured peaks -0.07 to -0.34 dBFS, zero clipped samples), and encoding material
// that hot can exceed the common -1 dBTP delivery ceiling. ~2 dB of headroom is a mix
// decision; the recordings themselves are clean and were not re-cut.
export const NARRATION_GAIN = 0.794;

/** Headroom in dB, for checks and for the render log. */
export const headroomDb = (gain = NARRATION_GAIN) => 20 * Math.log10(gain);
