// Volume popup. Shows the volume of a satellite's media or music player for a few seconds.
// Called from view_assist.js on volume_change and assist_state_change events.

const VOLUME_POPUP_SECONDS = 5;

class VolumePopup {
  constructor() {
    this.timeout = null;
    this.listening = false;
  }

  // Popup element inside the overlay shadow root, or null if the overlay is not loaded
  _popup() {
    return (
      document.querySelector("view-assist-overlays")?.shadowRoot?.querySelector("#volume_popup") ?? null
    );
  }

  show(volume) {
    // Not while the assistant is listening, or without a volume value
    if (this.listening || volume == null) return;

    // Restart the timer from this change, so the popup stays up for a fixed time after the last change
    clearTimeout(this.timeout);
    this.timeout = setTimeout(() => this.hide(), VOLUME_POPUP_SECONDS * 1000);

    const popup = this._popup();
    if (!popup) return;

    // Always the speaker icon, whichever player changed
    popup.querySelector("#speaker_icon").style.display = "inline-block";
    popup.querySelector("#music_icon").style.display = "none";
    popup.querySelector("#volume_text").textContent = `${volume}%`;
    // Shadow for the text and icon, applied here so the overlay CSS is unchanged
    popup.style.filter = "drop-shadow(0 0.3vh 0.6vh rgba(0, 0, 0, 0.8))";
    popup.style.display = "flex";
  }

  hide() {
    clearTimeout(this.timeout);
    this.timeout = null;
    const popup = this._popup();
    if (popup) popup.style.display = "none";
  }

  setListening(listening) {
    this.listening = listening;
    if (listening) this.hide();
  }
}

export const volumePopup = new VolumePopup();
