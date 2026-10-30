import {cancelRender, continueRender, delayRender} from 'remotion';
// Vendored Inter (latin subset, variable 100-900) so a render needs no network and the
// output does not depend on Google's CDN. Licensed under the SIL OFL 1.1 — see OFL.txt.
// The bundler emits this as an asset and gives us its URL.
import interUrl from './Inter-latin-variable.woff2';

export const FONT_FAMILY = 'Inter, ui-sans-serif, system-ui, sans-serif';

let loaded: Promise<unknown> | null = null;

/** Blocks the render until the vendored face is actually usable. */
export const loadInter = () => {
  if (loaded) return loaded;
  const handle = delayRender('Loading vendored Inter');
  const face = new FontFace('Inter', `url(${interUrl}) format('woff2')`, {weight: '100 900', style: 'normal'});
  loaded = face
    .load()
    .then((f) => {
      document.fonts.add(f);
      continueRender(handle);
    })
    .catch((err) => {
      // Rethrowing inside the promise is unobserved, so the render would carry on in a
      // fallback face and silently ship the wrong typography. Fail the render instead.
      cancelRender(err instanceof Error ? err : new Error(String(err)));
    });
  return loaded;
};
