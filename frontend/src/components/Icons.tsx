// Custom 16px line icons drawn for this product (no third-party icon set).
// Decorative by default (aria-hidden); pass `label` when an icon carries meaning on its own.
import { SVGProps } from "react";

type P = SVGProps<SVGSVGElement> & { label?: string; size?: number };

function Svg({ label, size = 16, children, ...rest }: P & { children: React.ReactNode }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.5}
      strokeLinecap="round" strokeLinejoin="round" role={label ? "img" : undefined} aria-label={label}
      aria-hidden={label ? undefined : true} focusable="false" {...rest}>
      {label ? <title>{label}</title> : null}
      {children}
    </svg>
  );
}

export const IconShield = (p: P) => <Svg {...p}><path d="M8 1.5 13.5 3.6v4.1c0 3.2-2.3 5.6-5.5 6.8C4.8 13.3 2.5 10.9 2.5 7.7V3.6Z" /><path d="M5.5 8h5" /></Svg>;
export const IconGauge = (p: P) => <Svg {...p}><path d="M2.5 11.5a5.5 5.5 0 1 1 11 0" /><path d="M8 11.5 10.5 6.5" /><path d="M2.5 13.5h11" /></Svg>;
export const IconAgent = (p: P) => <Svg {...p}><rect x="3" y="4.5" width="10" height="8" rx="1" /><path d="M8 4.5V2" /><path d="M6 8.5h.01M10 8.5h.01" strokeWidth={2} /><path d="M1.5 8v2M14.5 8v2" /></Svg>;
export const IconFlask = (p: P) => <Svg {...p}><path d="M6 1.5h4M6.5 1.5v4L2.8 12.4a1.2 1.2 0 0 0 1 1.6h8.4a1.2 1.2 0 0 0 1-1.6L9.5 5.5v-4" /><path d="M4.5 9.5h7" /></Svg>;
export const IconGate = (p: P) => <Svg {...p}><path d="M2 14V4l6-2.5L14 4v10" /><path d="M5.5 14V8h5v6" /><path d="M2 14h12" /></Svg>;
export const IconPulse = (p: P) => <Svg {...p}><path d="M1.5 8.5h3l1.5-4 2.5 8 2-6 1 2h3" /></Svg>;
export const IconRules = (p: P) => <Svg {...p}><path d="M5.5 3.5h8M5.5 8h8M5.5 12.5h8" /><path d="M2 3.5h1M2 8h1M2 12.5h1" strokeWidth={2} /></Svg>;
export const IconMagnifier = (p: P) => <Svg {...p}><circle cx="7" cy="7" r="4.5" /><path d="m10.5 10.5 3.5 3.5" /></Svg>;
export const IconBranch = (p: P) => <Svg {...p}><circle cx="4" cy="3.5" r="1.5" /><circle cx="4" cy="12.5" r="1.5" /><circle cx="12" cy="6.5" r="1.5" /><path d="M4 5v6M12 8c0 2.5-3 2.5-8 3" /></Svg>;
export const IconDoc = (p: P) => <Svg {...p}><path d="M3.5 1.5h6l3 3v10h-9Z" /><path d="M9.5 1.5v3h3M5.5 8h5M5.5 10.5h5" /></Svg>;
export const IconBars = (p: P) => <Svg {...p}><path d="M2 14h12" /><path d="M4 11V7M8 11V3.5M12 11V5.5" strokeWidth={2} /></Svg>;
export const IconLink = (p: P) => <Svg {...p}><path d="M6.5 9.5 9.5 6.5" /><path d="M7 4.5 8.6 2.9a2.8 2.8 0 0 1 4 4L11 8.5" /><path d="M9 11.5 7.4 13.1a2.8 2.8 0 0 1-4-4L5 7.5" /></Svg>;
export const IconPlug = (p: P) => <Svg {...p}><path d="M5.5 1.5v3M10.5 1.5v3" /><path d="M3.5 4.5h9v2.5a4.5 4.5 0 0 1-9 0Z" /><path d="M8 11.5v3" /></Svg>;
export const IconGear = (p: P) => <Svg {...p}><circle cx="8" cy="8" r="2.2" /><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4" /></Svg>;
export const IconMenu = (p: P) => <Svg {...p}><path d="M2 4h12M2 8h12M2 12h12" /></Svg>;
export const IconCheck = (p: P) => <Svg {...p}><path d="m3 8.5 3 3 7-7" /></Svg>;
export const IconBlock = (p: P) => <Svg {...p}><circle cx="8" cy="8" r="6" /><path d="M3.8 12.2 12.2 3.8" /></Svg>;
export const IconHold = (p: P) => <Svg {...p}><circle cx="8" cy="8" r="6" /><path d="M6.3 5.5v5M9.7 5.5v5" /></Svg>;
export const IconAlert = (p: P) => <Svg {...p}><path d="M8 2 14.5 13.5h-13Z" /><path d="M8 6.5v3" /><path d="M8 11.5h.01" strokeWidth={2} /></Svg>;
export const IconDash = (p: P) => <Svg {...p}><path d="M4 8h8" /></Svg>;
export const IconQuestion = (p: P) => <Svg {...p}><circle cx="8" cy="8" r="6" /><path d="M6.3 6.2a1.8 1.8 0 1 1 2.5 1.7c-.5.2-.8.6-.8 1.1v.4" /><path d="M8 11.5h.01" strokeWidth={2} /></Svg>;
export const IconRefresh = (p: P) => <Svg {...p}><path d="M13 7.5A5 5 0 0 0 4 4.5L2.5 6" /><path d="M2.5 2.5V6H6" /><path d="M3 8.5A5 5 0 0 0 12 11.5L13.5 10" /><path d="M13.5 13.5V10H10" /></Svg>;
export const IconDownload = (p: P) => <Svg {...p}><path d="M8 2v8.5M4.5 7 8 10.5 11.5 7" /><path d="M2.5 13.5h11" /></Svg>;
export const IconPlay = (p: P) => <Svg {...p}><path d="M4.5 2.5v11l9-5.5Z" /></Svg>;
export const IconPause = (p: P) => <Svg {...p}><path d="M5 3v10M11 3v10" strokeWidth={2} /></Svg>;
export const IconClose = (p: P) => <Svg {...p}><path d="m4 4 8 8M12 4l-8 8" /></Svg>;
