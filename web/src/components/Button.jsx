export default function Button({
  children,
  icon: Icon,
  variant = "secondary",
  size = "default",
  className = "",
  ...props
}) {
  const accessibleLabel = props["aria-label"] ?? (
    typeof children === "string" ? children : undefined
  );
  return (
    <button
      className={`button button--${variant} button--${size} ${className}`.trim()}
      {...props}
      aria-label={accessibleLabel}
    >
      {Icon ? <Icon aria-hidden="true" size={size === "icon" ? 17 : 16} /> : null}
      {children ? <span>{children}</span> : null}
    </button>
  );
}
