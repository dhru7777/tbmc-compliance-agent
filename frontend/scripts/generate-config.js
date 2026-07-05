#!/usr/bin/env node
/** Writes js/config.js from Netlify/Railway env at build time. */
const fs = require("fs");
const path = require("path");

const isNetlify = process.env.NETLIFY === "true";
const apiBase = (
  process.env.API_BASE ||
  process.env.VITE_API_BASE ||
  (isNetlify ? "" : "http://127.0.0.1:8000")
).replace(/\/$/, "");

if (isNetlify && !apiBase) {
  console.error(
    "Netlify build: set API_BASE to your Railway HTTPS URL (Site settings → Environment variables)."
  );
  process.exit(1);
}

if (isNetlify && !apiBase.startsWith("https://")) {
  console.error("Netlify build: API_BASE must use HTTPS so mobile browsers can reach the API.");
  process.exit(1);
}

const out = path.join(__dirname, "..", "js", "config.js");
const body = `// Generated at build — do not edit on Netlify.\nwindow.TBMC_CONFIG = { API_BASE: ${JSON.stringify(apiBase)} };\n`;
fs.writeFileSync(out, body, "utf8");
console.log("Wrote config.js with API_BASE:", apiBase);
