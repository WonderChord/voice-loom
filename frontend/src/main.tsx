import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./style.css";
import "./details.css";
import "./workspace.css";
async function start() {
  const session = new URLSearchParams(location.hash.slice(1)).get("session");
  if (session) {
    history.replaceState(null, "", location.pathname + location.search);
    const response = await fetch("/api/auth/bootstrap", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session }), credentials: "same-origin",
    });
    if (!response.ok) throw new Error("本机授权已失效，请从声织启动器重新打开");
  }
  ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
  );
}
void start().catch(() => {
  const root = document.getElementById("root")!;
  const text = document.createElement("p");
  text.textContent = "本机授权未完成，请关闭此页并从声织桌面程序或启动器重新打开。";
  root.replaceChildren(text);
});
