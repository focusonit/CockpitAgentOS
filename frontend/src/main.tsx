import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./cockpit/App";
import { PhoneApp } from "./phone/PhoneApp";
import "./index.css";

const root = document.getElementById("root");
if (!root) throw new Error("缺少 #root 节点");

createRoot(root).render(
  <StrictMode>
    {location.pathname.startsWith("/phone") ? <PhoneApp /> : <App />}
  </StrictMode>,
);
