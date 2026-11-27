// View Assist clock view (JS version of the button-card clock).
//
// Load as a module resource alongside the other View Assist js_modules.
// Card config:
//   type: custom:va-clock-card
//   satellite_entity: assist_satellite.kitchen   # optional, defaults to the View Assist satellite
//   weather_entity: weather.home                 # optional, defaults to the satellite's weather_entity
//   dashboard: /view-assist                      # optional, defaults to the satellite's dashboard
//   show_date: true                              # optional

const WEATHER_ICONS = {
  "clear-night": "mdi:weather-night",
  "cloudy": "mdi:weather-cloudy",
  "dust": "mdi:weather-dust",
  "fog": "mdi:weather-fog",
  "hail": "mdi:weather-hail",
  "hazy": "mdi:weather-hazy",
  "hurricane": "mdi:weather-hurricane",
  "lightning": "mdi:weather-lightning",
  "lightning-rainy": "mdi:weather-lightning-rainy",
  "partlycloudy": "mdi:weather-partly-cloudy",
  "partly-lightning": "mdi:weather-partly-lightning",
  "partly-rainy": "mdi:weather-partly-rainy",
  "partly-snowy": "mdi:weather-partly-snowy",
  "partly-snowy-rainy": "mdi:weather-partly-snowy-rainy",
  "pouring": "mdi:weather-pouring",
  "rainy": "mdi:weather-rainy",
  "snowy": "mdi:weather-snowy",
  "snowy-heavy": "mdi:weather-snowy-heavy",
  "snowy-rainy": "mdi:weather-snowy-rainy",
  "sunny": "mdi:weather-sunny",
  "tornado": "mdi:weather-tornado",
  "windy": "mdi:weather-windy",
  "windy-variant": "mdi:weather-windy-variant",
};

class VaClockCard extends HTMLElement {
  constructor() {
    super();
    this._config = {};
    this._hass = null;
    this._interval = null;
    this._built = false;
    this._last = {};
  }

  setConfig(config) {
    this._config = config || {};
    this._last = {};
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  getCardSize() {
    return 6;
  }

  connectedCallback() {
    this._build();
    this._update();
    this._interval = setInterval(() => this._update(), 1000);
  }

  disconnectedCallback() {
    clearInterval(this._interval);
    this._interval = null;
  }

  _build() {
    if (this._built) return;
    this._built = true;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host {
          display: block;
          height: 100%;
        }
        .card {
          position: relative;
          box-sizing: border-box;
          height: 100%;
          min-height: 300px;
          overflow: hidden;
          background-color: black;
          background-position: center;
          background-size: cover;
          background-repeat: no-repeat;
          color: #fff;
          font-weight: 300;
          display: grid;
          grid-template-areas:
            "title status"
            "time time"
            "date date";
          grid-template-rows: 15vh 50vh 15vh;
          grid-template-columns: 1fr 1fr;
          border-radius: var(--ha-card-border-radius, 12px);
        }
        .card.portrait {
          grid-template-rows: 15vh 35vh 15vh;
        }
        .title {
          grid-area: title;
          position: relative;
          z-index: 1;
          justify-self: start;
          align-self: start;
          display: flex;
          align-items: center;
          gap: 1vh;
          width: max-content;
          margin-left: 5%;
          margin-top: -5%;
          font-size: 15vh;
          font-weight: 300;
          color: #fff;
          cursor: pointer;
        }
        .card.portrait .title {
          font-size: 10vh;
        }
        .title ha-icon {
          --mdc-icon-size: 13vh;
          flex: 0 0 auto;
        }
        .card.portrait .title ha-icon {
          --mdc-icon-size: 10vh;
        }
        .title[hidden] {
          display: none;
        }
        .time {
          grid-area: time;
          align-self: center;
          justify-self: center;
          z-index: 1;
          font-size: 55vh;
          font-weight: bold;
          line-height: 1;
          color: #fff;
        }
        .card.portrait .time {
          font-size: 25vh;
        }
        .date {
          grid-area: date;
          align-self: center;
          justify-self: center;
          z-index: 1;
          font-size: 15vh;
          font-weight: 300;
          width: max-content;
          color: #fff;
        }
        .card.portrait .date {
          font-size: 8vh;
        }
        .shader {
          position: absolute;
          inset: 0;
          background-color: rgba(0, 0, 0, 0.15);
          pointer-events: none;
          display: none;
        }
        .night-mode .shader {
          display: block;
        }
        /* Night mode: time dims to 35% and turns red, title and date go transparent */
        .night-mode .time {
          color: red;
          opacity: 0.35;
        }
        .night-mode .title,
        .night-mode .date {
          color: transparent;
        }
      </style>
      <div class="card" id="card">
        <div class="title" id="title" hidden>
          <ha-icon id="w-icon"></ha-icon>
          <span id="w-temp"></span>
        </div>
        <div class="time" id="time"></div>
        <div class="date" id="date"></div>
        <div class="shader"></div>
      </div>
    `;
    this._els = {
      card: root.getElementById("card"),
      title: root.getElementById("title"),
      wIcon: root.getElementById("w-icon"),
      wTemp: root.getElementById("w-temp"),
      time: root.getElementById("time"),
      date: root.getElementById("date"),
    };
    this._els.title.addEventListener("click", () => this._openWeather());
  }

  _satEntityId() {
    return (
      this._config.satellite_entity ||
      localStorage.getItem("view_assist_sensor") ||
      ""
    );
  }

  _satState() {
    const id = this._satEntityId();
    return id ? this._hass?.states?.[id] : null;
  }

  _setText(key, el, value) {
    if (this._last[key] === value) return;
    this._last[key] = value;
    el.textContent = value;
  }

  _setHTML(key, el, value) {
    if (this._last[key] === value) return;
    this._last[key] = value;
    el.innerHTML = value;
  }

  _update() {
    if (!this._built || !this._hass) return;
    const sat = this._satState();
    const attrs = sat?.attributes || {};
    const { card } = this._els;

    const portrait = window.viewAssistResponsive?.orientation === "portrait";
    card.classList.toggle("portrait", portrait);

    const night = attrs.mode === "night";
    card.classList.toggle("night-mode", night);

    card.style.fontFamily = `"${attrs.font_style || "Roboto"}", sans-serif`;

    this._updateBackground(attrs.background, night);
    this._updateClock(attrs);
    this._updateWeather(attrs);
  }

  _updateBackground(background, night) {
    const image = night ? "" : background || "";
    if (this._last.background === image) return;
    this._last.background = image;
    this._els.card.style.background = image
      ? `center / cover no-repeat url(${image})`
      : "";
  }

  _updateClock(attrs) {
    const hour24 = this._config.hour24 ?? attrs.use_24_hour_time ?? false;

    this._setHTML(
      "time",
      this._els.time,
      `<viewassist-clock server_time=true mode='time' hour24=${hour24}></viewassist-clock>`
    );

    const dateHTML =
      this._config.show_date === false
        ? ""
        : `<viewassist-clock server_time=true mode="date"></viewassist-clock>`;
    this._setHTML("date", this._els.date, dateHTML);
  }

  _updateWeather(attrs) {
    const entityId = this._config.weather_entity || attrs.weather_entity;
    const state = entityId ? this._hass?.states?.[entityId] : null;
    const { title, wIcon, wTemp } = this._els;

    if (!state) {
      title.hidden = true;
      return;
    }
    title.hidden = false;

    const icon = WEATHER_ICONS[state.state] || "mdi:help";
    if (this._last.wIcon !== icon) {
      this._last.wIcon = icon;
      wIcon.setAttribute("icon", icon);
    }

    const temp = state.attributes.temperature;
    this._setText(
      "wTemp",
      wTemp,
      temp !== undefined ? `${temp}°` : ""
    );
  }

  _openWeather() {
    const sat = this._satEntityId();
    if (!sat) return;
    const dashboard =
      this._config.dashboard ||
      this._satState()?.attributes?.dashboard ||
      "/view-assist";
    this._hass.callService("view_assist", "navigate", {
      device: sat,
      path: `${dashboard}/weather`,
    });
  }
}

if (!customElements.get("va-clock-card")) {
  customElements.define("va-clock-card", VaClockCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "va-clock-card",
  name: "View Assist Clock",
  description: "Clock view with weather, date and night mode, converted from the button-card version.",
});