/* ==========================================================================
   va-fit-card
   Fits any Home Assistant card inside the view and keeps its proportions.

   The card is laid out at a fixed reference size (its designed size), then
   shrunk to fit the view. The card never sees a narrow space, so its layout
   (day names, spacing, proportions) stays intact.

   Config keys:
     card:                   any card configuration (required)
     aspect_ratio:           e.g. "16:9" (optional). Omit to fill the whole view.
     portrait_aspect_ratio:  e.g. "9:16" (optional). Used when the view is taller
                             than it is wide. Defaults to the inverse of aspect_ratio.
     background:             color of the empty space (optional, default #000000)
     height:                 CSS height of the wrapper (optional, default 80vh)
     debug:                  true shows a sizes box in the corner (optional)
   ========================================================================== */

// Internal reference size for the longer side of the card. Not a YAML setting.
const VA_FIT_REF_LONG = 1280;

// Accepts "16:9", "16/9", or a number such as 1.777. Returns width / height, or null.
function vaFitParseRatio(value) {
  if (value === undefined || value === null || value === "") return null;
  if (typeof value === "number") return value > 0 ? value : null;
  const m = String(value).trim().match(/^(\d+(?:\.\d+)?)\s*[:/]\s*(\d+(?:\.\d+)?)$/);
  if (!m) return null;
  const w = Number(m[1]);
  const h = Number(m[2]);
  return w > 0 && h > 0 ? w / h : null;
}

class VaFitCard extends HTMLElement {
  constructor() {
    super();
    this._root = this.attachShadow({ mode: "open" });
    this._root.innerHTML = `
      <style>
        :host { display: block; position: relative; width: 100%; height: 100%; overflow: hidden; }
        .outer { position: absolute; inset: 0; }
        .box { position: absolute; overflow: hidden; }
        .err { color: #fff; font: 14px sans-serif; padding: 12px; }
        .dbg { position: absolute; left: 0; top: 0; z-index: 99; background: rgba(200, 0, 0, 0.85); color: #fff; font: 12px monospace; padding: 4px 6px; white-space: pre; display: none; }
      </style>
      <div class="outer"><div class="box"></div><div class="dbg"></div></div>`;
    this._outer = this._root.querySelector(".outer");
    this._box = this._root.querySelector(".box");
    this._dbg = this._root.querySelector(".dbg");
    this._inner = null;
    this._cfg = null;
    this._hass = null;
    this._token = 0;
    this._ro = new ResizeObserver(() => this._layout());
  }

  setConfig(config) {
    if (!config || !config.card || typeof config.card !== "object") {
      throw new Error("va-fit-card: 'card' is required and must be a card configuration.");
    }
    // YAML reads an unquoted 16:9 as the number 969 (base 60), so catch that and explain it
    if (typeof config.aspect_ratio === "number" && (config.aspect_ratio > 4 || config.aspect_ratio < 0.25)) {
      throw new Error('va-fit-card: put the ratio in quotes in YAML, e.g. aspect_ratio: "16:9"');
    }
    if (typeof config.portrait_aspect_ratio === "number" && (config.portrait_aspect_ratio > 4 || config.portrait_aspect_ratio < 0.25)) {
      throw new Error('va-fit-card: put the ratio in quotes in YAML, e.g. portrait_aspect_ratio: "9:16"');
    }
    if (config.aspect_ratio !== undefined && vaFitParseRatio(config.aspect_ratio) === null) {
      throw new Error('va-fit-card: aspect_ratio must look like "16:9"');
    }
    if (config.portrait_aspect_ratio !== undefined && vaFitParseRatio(config.portrait_aspect_ratio) === null) {
      throw new Error('va-fit-card: portrait_aspect_ratio must look like "9:16"');
    }
    const landscape = vaFitParseRatio(config.aspect_ratio);
    let portrait = vaFitParseRatio(config.portrait_aspect_ratio);
    if (portrait === null && landscape !== null) portrait = 1 / landscape;

    this._cfg = {
      card: config.card,
      landscape,
      portrait,
      background: config.background ?? "#000000",
      height: config.height ?? "80vh",
      debug: config.debug === true
    };

    // A percentage height collapses inside a normal dashboard card, so set a real one.
    this.style.height = this._cfg.height;

    // Config changed: rebuild the inner card
    this._token++;
    this._inner = null;
    if (this.isConnected) this._build();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._inner) this._inner.hass = hass;
  }

  connectedCallback() {
    this._ro.observe(this);
    this._build();
  }

  disconnectedCallback() {
    this._ro.unobserve(this);
  }

  async _build() {
    if (!this._cfg || this._inner || !this.isConnected) return;
    const token = ++this._token;
    try {
      const helpers = await window.loadCardHelpers();
      const el = await helpers.createCardElement(this._cfg.card);
      if (token !== this._token || !this.isConnected) return;
      el.style.display = "block";
      el.style.position = "absolute";
      el.style.left = "0";
      el.style.top = "0";
      el.style.transformOrigin = "0 0";
      el.style.boxSizing = "border-box";
      this._box.replaceChildren(el);
      this._inner = el;
      if (this._hass) el.hass = this._hass;
      this._layout();
    } catch (err) {
      const msg = document.createElement("div");
      msg.className = "err";
      msg.textContent = "va-fit-card: " + (err && err.message ? err.message : String(err));
      this._box.replaceChildren(msg);
    }
  }

  _layout() {
    if (!this._cfg) return;
    this._outer.style.background = this._cfg.background;

    const W = this.clientWidth || window.innerWidth;
    const H = this.clientHeight || window.innerHeight * 0.8;
    if (!W || !H) return;

    const viewRatio = W / H;
    const portrait = H > W;
    // With no aspect_ratio, the card's shape is the view's own shape (fill)
    const ratio = (portrait ? this._cfg.portrait : this._cfg.landscape) ?? viewRatio;

    // Fitted box, as percentages of the view
    let w = 100;
    let h = 100;
    if (viewRatio > ratio) {
      h = 100;
      w = (ratio / viewRatio) * 100;
    } else {
      w = 100;
      h = (viewRatio / ratio) * 100;
    }
    this._box.style.width = w + "%";
    this._box.style.height = h + "%";
    this._box.style.left = (100 - w) / 2 + "%";
    this._box.style.top = (100 - h) / 2 + "%";

    // Reference layout size: the card is laid out at this size, then scaled to the box
    let refW;
    let refH;
    if (ratio >= 1) {
      refW = VA_FIT_REF_LONG;
      refH = VA_FIT_REF_LONG / ratio;
    } else {
      refH = VA_FIT_REF_LONG;
      refW = VA_FIT_REF_LONG * ratio;
    }
    const boxWidthInView = (w / 100) * W;
    const scale = boxWidthInView / refW;

    if (this._inner) {
      this._inner.style.width = refW + "px";
      this._inner.style.height = refH + "px";
      this._inner.style.transform = "scale(" + scale + ")";
    }

    if (this._cfg.debug) {
      const inner = this._inner ? this._inner.tagName.toLowerCase() : "none";
      this._dbg.style.display = "block";
      this._dbg.textContent =
        "view W=" + W + " H=" + H + " portrait=" + portrait + "\n" +
        "ratio=" + ratio.toFixed(3) + " box=" + w.toFixed(1) + "% x " + h.toFixed(1) + "%\n" +
        "reference=" + Math.round(refW) + " x " + Math.round(refH) + " scale=" + scale.toFixed(3) + "\n" +
        "inner=" + inner;
    } else {
      this._dbg.style.display = "none";
    }
  }
}

if (!customElements.get("va-fit-card")) {
  customElements.define("va-fit-card", VaFitCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "va-fit-card",
  name: "VA Fit Card",
  description: "Fits any card inside the view at its designed layout"
});