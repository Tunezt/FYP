/** Hand-drawn inline icon set — one consistent 24px grid, 1.7px strokes,
 * rounded caps. Deliberately not an icon library. */

type IconProps = { className?: string };

function Svg({ children, className }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className ?? "h-5 w-5"}
      aria-hidden
    >
      {children}
    </svg>
  );
}

export const IconHome = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 11.5 12 4l8 7.5" />
    <path d="M6.5 10.5V20h11v-9.5" />
    <path d="M10 20v-5h4v5" />
  </Svg>
);

export const IconChart = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 19.5h16" />
    <path d="M6.5 19.5v-6" />
    <path d="M11 19.5V8.5" />
    <path d="M15.5 19.5v-8" />
    <path d="M20 19.5v-13" />
  </Svg>
);

export const IconBox = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3.5 20 7.5v9l-8 4-8-4v-9l8-4Z" />
    <path d="M4 7.5l8 4 8-4" />
    <path d="M12 11.5v9" />
  </Svg>
);

export const IconWallet = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 7.5A2.5 2.5 0 0 1 6.5 5h11A2.5 2.5 0 0 1 20 7.5v9A2.5 2.5 0 0 1 17.5 19h-11A2.5 2.5 0 0 1 4 16.5v-9Z" />
    <path d="M15 12h5" />
    <circle cx="15.5" cy="12" r="0.5" fill="currentColor" />
  </Svg>
);

export const IconBell = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6 10a6 6 0 0 1 12 0c0 4 1.5 5.5 1.5 5.5h-15S6 14 6 10Z" />
    <path d="M10 18.5a2 2 0 0 0 4 0" />
  </Svg>
);

export const IconGear = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 3.5v2.2M12 18.3v2.2M20.5 12h-2.2M5.7 12H3.5M18 6l-1.6 1.6M7.6 16.4 6 18M18 18l-1.6-1.6M7.6 7.6 6 6" />
  </Svg>
);

export const IconSun = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2.5v2M12 19.5v2M21.5 12h-2M4.5 12h-2M18.4 5.6l-1.4 1.4M7 17l-1.4 1.4M18.4 18.4 17 17M7 7 5.6 5.6" />
  </Svg>
);

export const IconMoon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" />
  </Svg>
);

export const IconPlus = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 5v14M5 12h14" />
  </Svg>
);

export const IconCheck = (p: IconProps) => (
  <Svg {...p}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </Svg>
);

export const IconCopy = (p: IconProps) => (
  <Svg {...p}>
    <rect x="8.5" y="8.5" width="11" height="11" rx="2" />
    <path d="M5.5 15.5A1.5 1.5 0 0 1 4 14V6a2 2 0 0 1 2-2h8a1.5 1.5 0 0 1 1.5 1.5" />
  </Svg>
);

export const IconChat = (p: IconProps) => (
  <Svg {...p}>
    <path d="M20 12a8 8 0 1 0-14.9 4L4 20l4.2-1.1A8 8 0 0 0 20 12Z" />
    <path d="M8.5 11h7M8.5 14h4" />
  </Svg>
);

export const IconArrowUp = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 19V5M6 11l6-6 6 6" />
  </Svg>
);

export const IconArrowDown = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 5v14M6 13l6 6 6-6" />
  </Svg>
);

export const IconReceipt = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6 3.5h12V20l-2-1.3L14 20l-2-1.3L10 20l-2-1.3L6 20V3.5Z" />
    <path d="M9 8h6M9 11.5h6M9 15h3.5" />
  </Svg>
);

export const IconLock = (p: IconProps) => (
  <Svg {...p}>
    <rect x="5.5" y="10.5" width="13" height="9" rx="2" />
    <path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" />
  </Svg>
);

export const IconSpark = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3.5 13.8 9l5.7.2-4.5 3.5 1.6 5.5L12 14.8 7.4 18.2 9 12.7 4.5 9.2 10.2 9 12 3.5Z" />
  </Svg>
);

export const IconShop = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4.5 9.5 6 4.5h12l1.5 5" />
    <path d="M4.5 9.5h15v2a2.2 2.2 0 0 1-2.2 2.2c-1.3 0-2.3-1-2.3-2.2 0 1.2-1 2.2-2.5 2.2S10 12.7 10 11.5c0 1.2-1 2.2-2.3 2.2A2.2 2.2 0 0 1 5.5 11.5" />
    <path d="M6 13.5V20h12v-6.5" />
    <path d="M9.5 20v-4h5v4" />
  </Svg>
);

export const IconLogout = (p: IconProps) => (
  <Svg {...p}>
    <path d="M13.5 4.5H7a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h6.5" />
    <path d="M16 8.5 19.5 12 16 15.5" />
    <path d="M9.5 12h10" />
  </Svg>
);

export const IconChevronRight = (p: IconProps) => (
  <Svg {...p}>
    <path d="m9.5 5.5 6.5 6.5-6.5 6.5" />
  </Svg>
);

export const IconChevronLeft = (p: IconProps) => (
  <Svg {...p}>
    <path d="m14.5 5.5-6.5 6.5 6.5 6.5" />
  </Svg>
);

export const IconCalendar = (p: IconProps) => (
  <Svg {...p}>
    <rect x="3.5" y="5" width="17" height="15.5" rx="3" />
    <path d="M3.5 9.5h17M8 3.5v3M16 3.5v3" />
  </Svg>
);

export const IconChevronDown = (p: IconProps) => (
  <Svg {...p}>
    <path d="m5.5 9.5 6.5 6.5 6.5-6.5" />
  </Svg>
);

export const IconTrendUp = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 17.5 9.5 12l3.5 3.5 7-7.5" />
    <path d="M15.5 8h4.5v4.5" />
  </Svg>
);

export const IconClose = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6.5 6.5l11 11M17.5 6.5l-11 11" />
  </Svg>
);

export const IconSearch = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m16 16 4 4" />
  </Svg>
);

export const IconMore = (p: IconProps) => (
  <Svg {...p}>
    <rect x="4" y="4" width="6.5" height="6.5" rx="1.8" />
    <rect x="13.5" y="4" width="6.5" height="6.5" rx="1.8" />
    <rect x="4" y="13.5" width="6.5" height="6.5" rx="1.8" />
    <rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.8" />
  </Svg>
);

export const IconBackspace = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9 5.5h10a1.5 1.5 0 0 1 1.5 1.5v10a1.5 1.5 0 0 1-1.5 1.5H9L3.5 12 9 5.5Z" />
    <path d="m11.5 9.5 5 5M16.5 9.5l-5 5" />
  </Svg>
);

export const IconPrinter = (p: IconProps) => (
  <Svg {...p}>
    <path d="M7 9V4h10v5" />
    <rect x="3.5" y="9" width="17" height="8" rx="2" />
    <path d="M7 14h10v6H7z" />
  </Svg>
);

export const IconDownload = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 4v11M7.5 10.5 12 15l4.5-4.5" />
    <path d="M5 19.5h14" />
  </Svg>
);

export const IconUpload = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 15.5v-11M7.5 9 12 4.5 16.5 9" />
    <path d="M5 19.5h14" />
  </Svg>
);

export const IconExternal = (p: IconProps) => (
  <Svg {...p}>
    <path d="M13.5 5h5.5v5.5M19 5l-8 8" />
    <path d="M17 13.5V18a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 18V8.5A1.5 1.5 0 0 1 6 7h4.5" />
  </Svg>
);

export const IconPlugOff = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9 3.5v4M15 3.5v4" />
    <path d="M6.5 7.5h11V11a5.5 5.5 0 0 1-11 0V7.5Z" />
    <path d="M12 16.5v4" />
    <path d="M4 4l16 16" />
  </Svg>
);

export const IconTicket = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 7.5A1.5 1.5 0 0 1 5.5 6h13A1.5 1.5 0 0 1 20 7.5v2a2.5 2.5 0 0 0 0 5v2a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 16.5v-2a2.5 2.5 0 0 0 0-5v-2Z" />
    <path d="M14 6.5v11" strokeDasharray="1.5 2" />
  </Svg>
);

export const IconCamera = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 8.5A1.5 1.5 0 0 1 5.5 7h2l1.5-2h6l1.5 2h2A1.5 1.5 0 0 1 20 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5v-9Z" />
    <circle cx="12" cy="12.5" r="3.5" />
  </Svg>
);

export const IconShield = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3.5 19 6v5.5c0 4.2-3 7.6-7 9-4-1.4-7-4.8-7-9V6l7-2.5Z" />
    <path d="m9 12 2.2 2.2L15.5 10" />
  </Svg>
);

export const IconClock = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8" />
    <path d="M12 7.5V12l3 2" />
  </Svg>
);

export const IconNote = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6.5 3.5h8l4 4v11.5a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 5.5 19V5a1.5 1.5 0 0 1 1-1.5Z" />
    <path d="M14 3.5V8h4.5M8.5 12.5h7M8.5 16h5" />
  </Svg>
);

export const IconLeaf = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5 19c0-8 5-13.5 14-14-.5 9-6 14-14 14Z" />
    <path d="M5 19c3-3 6-5.5 9.5-7.5" />
  </Svg>
);

export const IconAlert = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8" />
    <path d="M12 8v4.5M12 15.8v.2" />
  </Svg>
);

/* ── Product-category icons ── same 24px grid/stroke as above, used by ItemIcon
 * to give stock rows a recognizable mark instead of bare initials. */

export const IconCatOil = (p: IconProps) => (
  <Svg {...p}>
    <path d="M10 3.5h4" />
    <path d="M10 3.5v2.3l-1.5 1.4A2 2 0 0 0 8 8.7V18a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2V8.7a2 2 0 0 0-.5-1.5L14 5.8V3.5" />
    <path d="M8 11.5h8" />
  </Svg>
);

export const IconCatRice = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4.5 11.5h15v.4a7.5 7.5 0 0 1-15 0v-.4Z" />
    <path d="M5.5 11.5 7 8.5h10l1.5 3" />
    <path d="M12 20v1.5" />
  </Svg>
);

export const IconCatNoodle = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4.5 11.5h15a7.5 7.5 0 0 1-15 0Z" />
    <path d="M7 11.5c1-2.2 3-3.3 5-3.3s4 1.1 5 3.3" />
    <path d="m13.5 5 5.5 5" />
    <path d="m15.5 3.5 5.5 5" />
  </Svg>
);

export const IconCatSugar = (p: IconProps) => (
  <Svg {...p}>
    <rect x="4.5" y="11" width="7.5" height="7.5" rx="1" />
    <rect x="12" y="6.5" width="7.5" height="7.5" rx="1" />
  </Svg>
);

export const IconCatMilk = (p: IconProps) => (
  <Svg {...p}>
    <path d="M8 9 12 5l4 4v9.5a1.5 1.5 0 0 1-1.5 1.5h-5A1.5 1.5 0 0 1 8 18.5V9Z" />
    <path d="M12 5v4M8 9h8" />
    <path d="M10.5 13h3v3h-3z" />
  </Svg>
);

export const IconCatCoffee = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5.5 9.5h10v4a4.5 4.5 0 0 1-4.5 4.5H10a4.5 4.5 0 0 1-4.5-4.5v-4Z" />
    <path d="M15.5 11h1.6a2 2 0 0 1 0 4h-1.6" />
    <path d="M8 6.5c-.2-.7.3-1.2.6-1.8M11.5 6.5c-.2-.7.3-1.2.6-1.8" />
  </Svg>
);

export const IconCatTea = (p: IconProps) => (
  <Svg {...p}>
    <path d="M7.5 8h8l-.9 10.2A1.5 1.5 0 0 1 13.1 19.5H9.9a1.5 1.5 0 0 1-1.5-1.3L7.5 8Z" />
    <path d="M13 8v-.5a2 2 0 0 1 2-2h1.4" />
    <rect x="15.5" y="4" width="2.6" height="2.4" rx="0.5" />
  </Svg>
);

export const IconCatEgg = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3.5c3 0 5.5 5.2 5.5 9.2a5.5 5.5 0 0 1-11 0c0-4 2.5-9.2 5.5-9.2Z" />
  </Svg>
);

export const IconCatGas = (p: IconProps) => (
  <Svg {...p}>
    <rect x="7" y="7.5" width="10" height="12.5" rx="2.5" />
    <path d="M10 7.5V6a2 2 0 0 1 4 0v1.5" />
    <path d="M9.5 4.5h5" />
  </Svg>
);

export const IconCatCleaning = (p: IconProps) => (
  <Svg {...p}>
    <path d="M10 9.5h4.5a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2v-6.5a2 2 0 0 1 2-2Z" />
    <path d="M10 9.5V6.5h3.5l3-2" />
    <path d="M17.5 4.5h1.5M18 7h1.5" />
  </Svg>
);

export const IconCatCigarette = (p: IconProps) => (
  <Svg {...p}>
    <rect x="7" y="6" width="10" height="14" rx="1.5" />
    <path d="M7 10.5h10" />
    <path d="M10 6V4.5h4V6" />
  </Svg>
);

export const IconCatFlour = (p: IconProps) => (
  <Svg {...p}>
    <path d="M8 8.5c0-1.4 1.8-2 4-2s4 .6 4 2V18a2 2 0 0 1-2 2h-4a2 2 0 0 1-2-2V8.5Z" />
    <path d="M9.5 6.8 8.5 4.5h7l-1 2.3" />
    <path d="M10 12.5h4" />
  </Svg>
);

export const IconCatWater = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9.5 6.5h5V4.5h-5Z" />
    <path d="M9 6.5h6v11.5a1.5 1.5 0 0 1-1.5 1.5h-3A1.5 1.5 0 0 1 9 18V6.5Z" />
    <path d="M9 13c1.5-1.2 4.5-1.2 6 0" />
  </Svg>
);

export const IconCatSauce = (p: IconProps) => (
  <Svg {...p}>
    <path d="M10 8.5h4l1 3v7a1.5 1.5 0 0 1-1.5 1.5h-3A1.5 1.5 0 0 1 9 18.5v-7l1-3Z" />
    <path d="M11 8.5V6h2v2.5" />
    <path d="M11.5 6V4h1v2" />
  </Svg>
);

export const IconCatSnack = (p: IconProps) => (
  <Svg {...p}>
    <path d="m7.5 5.5 1.5 1.6h6l1.5-1.6" />
    <path d="M8.5 7.1h7v9.8l1 1.6H7.5l1-1.6V7.1Z" />
    <path d="M10 11h4" />
  </Svg>
);

export const IconCatProduce = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 20v-7.5" />
    <path d="M12 12.5c-3 0-5-2-5-5 3 0 5 2 5 5Z" />
    <path d="M12 11c0-3 2-5 5-5 0 3-2 5-5 5Z" />
  </Svg>
);

export const IconCatBakery = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4.5 14.5a7.5 4 0 0 1 15 0v2a1.5 1.5 0 0 1-1.5 1.5H6a1.5 1.5 0 0 1-1.5-1.5v-2Z" />
    <path d="M9 14.2c.2-1.4.9-2.4 1.7-3.2M12 14.2c.2-1.4.9-2.4 1.7-3.2M15 14.5c.2-1.2.6-2 1.2-2.7" />
  </Svg>
);

export const IconUsers = (p: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <circle cx="9" cy="8" r="3.2" />
    <path d="M3.5 19a5.5 5.5 0 0 1 11 0" />
    <circle cx="16.5" cy="9.5" r="2.4" />
    <path d="M15 14.6a4.6 4.6 0 0 1 5.5 4.4" />
  </svg>
);
