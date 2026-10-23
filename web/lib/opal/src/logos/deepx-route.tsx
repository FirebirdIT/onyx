import type { IconProps } from "@opal/types";

// Deep X Route mark: the Deep X "X" glyph, stroked in the current text color
// so it follows the theme.
function SvgDeepXRoute({ size, ...props }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      {...props}
    >
      <path
        d="M5 5h5l12 22h5M5 27h5L22 5h5"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default SvgDeepXRoute;
