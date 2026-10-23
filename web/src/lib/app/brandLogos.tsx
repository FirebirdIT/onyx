import { SvgOnyxLogo, SvgOnyxLogoTyped } from "@opal/logos";
import type { IconProps } from "@opal/types";
import { BRAND } from "@/lib/brand";

const DEEPX_MARK_PATH = "M5 5h5l12 22h5M5 27h5L22 5h5";

function SvgDeepXMark({ size, ...props }: IconProps) {
  return (
    <svg
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      {...props}
    >
      <path
        d={DEEPX_MARK_PATH}
        stroke="var(--theme-primary-05)"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

interface SvgDeepXLogoTypedProps {
  size?: number;
  className?: string;
}

function SvgDeepXLogoTyped({ size, className }: SvgDeepXLogoTypedProps) {
  return (
    <svg
      height={size}
      viewBox="0 0 112 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      role="img"
      aria-label={BRAND.name}
    >
      <path
        d={DEEPX_MARK_PATH}
        stroke="var(--theme-primary-05)"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <text
        x="38"
        y="23"
        fontSize="20"
        fontWeight="600"
        fill="var(--text-05)"
        fontFamily="inherit"
      >
        {BRAND.name}
      </text>
    </svg>
  );
}

/** The product mark for the build-time brand. */
export function SvgBrandMark(props: IconProps) {
  return BRAND.id === "deepx" ? (
    <SvgDeepXMark {...props} />
  ) : (
    <SvgOnyxLogo {...props} />
  );
}

/** The product mark plus wordmark for the build-time brand. */
export function SvgBrandLogoTyped(props: SvgDeepXLogoTypedProps) {
  return BRAND.id === "deepx" ? (
    <SvgDeepXLogoTyped {...props} />
  ) : (
    <SvgOnyxLogoTyped {...props} />
  );
}
