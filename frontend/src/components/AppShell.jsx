import Sidebar from "./Sidebar.jsx";

export default function AppShell({ children }) {
  return (
    <div className="shell">
      <Sidebar />
      <main className="main">{children}</main>
    </div>
  );
}
