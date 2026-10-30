// The media manifest contract. Paths are versioned and immutable; the manifest
// itself is written last and is never one of the hashed files.

export const MEDIA_FILES = ['intro.mp4', 'walkthrough.mp4', 'intro.vtt', 'walkthrough.vtt', 'poster.jpg', 'chapters.json'];
export const MANIFEST_NAME = 'manifest.json';

export const mediaPath = (version, name) => `/guide-media/${version}/${name}`;

/**
 * @param {{version:string, chapters:{id:string,title:string,start:number}[],
 *          durations:{intro:number,walkthrough:number}, hashes:Record<string,string>}} input
 */
export function buildManifest({version, chapters, durations, hashes}) {
  if (!/^guide-\d{8}-v\d+$/.test(version)) throw new Error(`bad media version: ${version}`);
  const missing = MEDIA_FILES.filter((f) => !hashes[f]);
  if (missing.length) throw new Error(`cannot build manifest, unhashed files: ${missing.join(', ')}`);
  if (hashes[MANIFEST_NAME]) throw new Error('the manifest must not hash itself');
  const film = (name, duration) => {
    if (!(duration > 0)) throw new Error(`${name} has no measured duration`);
    return {
      src: mediaPath(version, `${name}.mp4`),
      poster: mediaPath(version, 'poster.jpg'),
      captions: mediaPath(version, `${name}.vtt`),
      duration: Math.round(duration * 1000) / 1000,
    };
  };
  return {
    version,
    intro: film('intro', durations.intro),
    walkthrough: film('walkthrough', durations.walkthrough),
    chapters,
    files: MEDIA_FILES.map((name) => ({name, sha256: hashes[name]})),
  };
}
