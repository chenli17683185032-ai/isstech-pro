export default function EmptyState({ icon: Icon, title, action }) {
  return (
    <div className="empty-state">
      {Icon ? <Icon size={22} aria-hidden="true" /> : null}
      <p>{title}</p>
      {action}
    </div>
  );
}
