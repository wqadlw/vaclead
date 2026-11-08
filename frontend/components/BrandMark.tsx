/**
 * vaclead 品牌标志（Logo 定稿：六角涡）——六角=螺栓/法兰的工业语言，
 * 内三叶=旋转的涡。hexagon 描边走 currentColor（随主题明暗自动反转），
 * 涡叶走 --accent（暗色主题自动提亮为 #7b86e8）。全站品牌位统一引用。
 */
export default function BrandMark({
  size = 32,
  className = "",
  hexColor,
  vortexColor = "var(--accent)",
}: {
  size?: number;
  className?: string;
  hexColor?: string;
  vortexColor?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      role="img"
      aria-label="vaclead"
      className={className}
      style={hexColor ? { color: hexColor } : undefined}
    >
      <path
        d="M 24 3.5 L 41.6 13.7 L 41.6 34.3 L 24 44.5 L 6.4 34.3 L 6.4 13.7 Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.8"
        strokeLinejoin="round"
      />
      <g fill={vortexColor}>
        <path d="M 24 24 L 33 10.5 L 37 24 Z" />
        <path d="M 24 24 L 37 24 L 24 37.5 Z" opacity="0.55" />
        <path d="M 24 24 L 15 10.5 L 24 10.5 Z" opacity="0.8" />
      </g>
    </svg>
  );
}
