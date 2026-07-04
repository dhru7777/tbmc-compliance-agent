#!/usr/bin/env node
/** Writes js/config.js from Netlify/Railway env at build time. */
const fs = require("fs");
const path = require("path");

const apiBase = (
  process.env.API_BASE ||
  process.env.VITE_API_BASE ||
  "http://127.0.0.1:8000"
).replace(/\/$/, "");

const out = path.join(__dirname, "..", "js", "config.js");
const body = `// Generated at build — do not edit on Netlify.\nwindow.TBMC_CONFIG = { API_BASE: ${JSON.stringify(apiBase)} };\n`;
fs.writeFileSync(out, body, "utf8");
console.log("Wrote config.js with API_BASE:", apiBase);
