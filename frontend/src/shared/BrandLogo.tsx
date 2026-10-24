import {useId} from 'react';
import logoSource from '../assets/brand-logo-source.png';
import './BrandLogo.css';

/**
 * Render the supplied artwork, including its original margins and gradients.
 * The source is opaque blue/cyan on near-white, so its alpha cannot be used
 * directly as a mask. Knock out the neutral background before theme colouring.
 * Both masks use the same unmodified raster; no paths are traced or redesigned.
 */
export function BrandLogo() {
  const id = useId().replace(/:/g, '');
  const silhouette = `${id}-silhouette`;
  const tonal = `${id}-tonal`;
  const knockout = `${id}-knockout`;
  const tone = `${id}-tone`;
  return <svg className="brand-logo" viewBox="0 0 1364 1065" width="1364" height="1065"
    preserveAspectRatio="xMidYMid meet" aria-hidden="true" focusable="false">
    <defs>
      <filter id={knockout} x="0" y="0" width="100%" height="100%" colorInterpolationFilters="sRGB">
        {/* White matte: alpha = clamp((245 - source red byte) / 235).
            Retain fractional edge coverage; remove near-white paper texture. */}
        <feColorMatrix in="SourceGraphic" type="matrix" result="coverage" values="
          0 0 0 0 1
          0 0 0 0 1
          0 0 0 0 1
          -1.085106383 0 0 0 1.042553191"/>
        {/* Reject neutral background, including grey texture; the artwork is
            blue/cyan. Interior chroma is well above this 3–11 byte transition. */}
        <feColorMatrix in="SourceGraphic" type="matrix" result="chroma" values="
          0 0 0 0 1
          0 0 0 0 1
          0 0 0 0 1
          -31.875 0 31.875 0 -0.375"/>
        <feComposite in="coverage" in2="chroma" operator="in"/>
      </filter>
      <filter id={tone} x="0" y="0" width="100%" height="100%" colorInterpolationFilters="sRGB">
        {/* Source cyan/blue separation preserves the supplied local gradients,
            rather than laying a new directional gradient over the artwork. */}
        <feColorMatrix type="matrix" values="
          0 0 0 0 1
          0 0 0 0 1
          0 0 0 0 1
          -1.7 1.7 0 0 -0.12"/>
      </filter>
      <mask id={silhouette} maskUnits="userSpaceOnUse" x="0" y="0" width="1364" height="1065" style={{maskType:'alpha'}}>
        <image href={logoSource} width="1364" height="1065" filter={`url(#${knockout})`}/>
      </mask>
      <mask id={tonal} maskUnits="userSpaceOnUse" x="0" y="0" width="1364" height="1065" style={{maskType:'alpha'}}>
        <image href={logoSource} width="1364" height="1065" filter={`url(#${tone})`}/>
      </mask>
    </defs>
    <g mask={`url(#${silhouette})`}>
      <rect width="1364" height="1065" fill="var(--brand-logo-ink)"/>
      <rect width="1364" height="1065" fill="var(--brand-logo-highlight)" mask={`url(#${tonal})`}/>
    </g>
  </svg>;
}
