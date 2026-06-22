import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import Landing from "./components/Landing";
import Start from "./components/Start";
import Inspection from "./components/Inspection";
import ReviewGate from "./components/ReviewGate";
import Result from "./components/Result";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/start" element={<Start />} />
        <Route path="/job/:id" element={<Inspection />} />
        <Route path="/review/:id" element={<ReviewGate />} />
        <Route path="/result/:id" element={<Result />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
);
