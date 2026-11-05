const PATHS = {
  plus: <path d="M10 4v12M4 10h12" />,
  chat: (
    <path d="M3.5 6.8A2.8 2.8 0 0 1 6.3 4h7.4a2.8 2.8 0 0 1 2.8 2.8v4.4a2.8 2.8 0 0 1-2.8 2.8H9.2l-3.9 2.8v-2.9a2.8 2.8 0 0 1-1.8-2.6z" />
  ),
  box: (
    <>
      <path d="M10 2.6 16.8 6v8L10 17.4 3.2 14V6z" />
      <path d="M3.4 6.1 10 9.5l6.6-3.4M10 9.5v7.8" />
    </>
  ),
  clock: (
    <>
      <circle cx="10" cy="10" r="6.6" />
      <path d="M10 6.2V10l2.6 1.7" />
    </>
  ),
  doc: (
    <>
      <path d="M5.2 3.2h6.1L15 6.9v9.9H5.2z" />
      <path d="M11 3.4V7h3.8M7.6 10.2h5M7.6 13h5" />
    </>
  ),
  user: (
    <>
      <circle cx="10" cy="7.4" r="2.8" />
      <path d="M4.6 16.4a5.6 5.6 0 0 1 10.8 0" />
    </>
  ),
  send: <path d="M3.2 10 16.6 3.6l-4 13.1-3.1-4.8z" />,
  eye: (
    <>
      <path d="M2.4 10S5.2 5.4 10 5.4 17.6 10 17.6 10 14.8 14.6 10 14.6 2.4 10 2.4 10z" />
      <circle cx="10" cy="10" r="2.1" />
    </>
  ),
  "eye-off": (
    <>
      <path d="M3.2 10S6 5.8 10 5.8c1 0 1.9.2 2.7.6M16.4 8.3c.7.8 1.2 1.7 1.2 1.7S14.8 14.2 10 14.2c-1 0-1.9-.2-2.7-.6" />
      <path d="M3.4 3.4 16.6 16.6" />
    </>
  ),
  info: (
    <>
      <circle cx="10" cy="10" r="6.8" />
      <path d="M10 9v4.4M10 6.6v.1" />
    </>
  ),
  "chevron-right": <path d="m7.8 4.6 5.2 5.4-5.2 5.4" />,
  "arrow-left": <path d="M16 10H4.4M9 5.2 4.2 10l4.8 4.8" />,
  close: <path d="m5.4 5.4 9.2 9.2M14.6 5.4l-9.2 9.2" />,
  check: <path d="m4.4 10.4 3.6 3.6 7.6-8" />,
  refresh: (
    <>
      <path d="M16.2 10a6.2 6.2 0 1 1-1.9-4.5" />
      <path d="M16.4 3.6v4h-4" />
    </>
  ),
};

export default function Icon({ name, size = 18, className = "", strokeWidth = 1.6, style }) {
  return (
    <svg
      className={className}
      style={style}
      width={size}
      height={size}
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {PATHS[name] ?? null}
    </svg>
  );
}

export function BrandMark({ size = 28 }) {
  return (
    <span className="brand-mark" style={{ width: size, height: size }} aria-hidden="true">
      <span className="brand-mark__back" />
      <span className="brand-mark__front" />
    </span>
  );
}

export function PackageGlyph({ size = 44 }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      stroke="#486a9c"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M24 6.5 40 14v20L24 41.5 8 34V14z" />
      <path d="M8.6 14.3 24 21.8l15.4-7.5M24 21.8v19.4" />
      <path d="M16 10.3l15.6 7.6v6.4" />
    </svg>
  );
}
