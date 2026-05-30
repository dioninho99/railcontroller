/* RailController – app.js
   WebSocket-Verbindung, Systemsteuerung, globale Hilfsfunktionen */

(function() {
  'use strict';

  // ── WebSocket ───────────────────────────────

  let ws = null;
  let wsRetryDelay = 2000;
  let wsRetryTimer = null;

  function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws`);

    ws.onopen = function() {
      console.log('[RC] WebSocket verbunden');
      wsRetryDelay = 2000;
      setZ21Status(null); // Status kommt per full_state
    };

    ws.onmessage = function(e) {
      try {
        const state = JSON.parse(e.data);
        applyState(state);
        // Seiten-spezifischer Handler
        if (typeof window.onStateUpdate === 'function') {
          window.onStateUpdate(state);
        }
      } catch(err) {
        console.warn('[RC] WS parse error', err);
      }
    };

    ws.onerror = function() {
      setZ21Status(false);
    };

    ws.onclose = function() {
      setZ21Status(false);
      ws = null;
      wsRetryTimer = setTimeout(() => connectWS(), wsRetryDelay);
      wsRetryDelay = Math.min(wsRetryDelay * 1.5, 15000);
    };

    // Keepalive Ping alle 20s
    setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send('ping');
      }
    }, 20000);
  }

  // ── State anwenden ──────────────────────────

  function applyState(state) {
    const t = state.type;

    if (t === 'full_state' || t === 'system') {
      if (state.track_power !== undefined) setTrackPower(state.track_power, state.emergency_stop);
      if (state.z21_connected !== undefined) setZ21Status(state.z21_connected);
    }

    if (t === 'emergency_stop') {
      setTrackPower(state.track_power || false, true);
    }
  }

  function setTrackPower(on, estop) {
    const badge = document.getElementById('z21-status');
    if (!badge) return;
    if (estop) {
      badge.textContent = 'NOTHALT';
      badge.className = 'status-badge status-warning';
    }
  }

  function setZ21Status(online) {
    const badge = document.getElementById('z21-status');
    if (!badge) return;
    if (online === null) return;
    if (online) {
      badge.textContent = 'Z21 online';
      badge.className = 'status-badge status-online';
    } else {
      badge.textContent = 'Z21 offline';
      badge.className = 'status-badge status-offline';
    }
  }

  // ── Systemsteuerung (global) ────────────────

  window.powerOn = async function() {
    await fetch('/api/system/power/on', {method: 'POST'});
  };

  window.powerOff = async function() {
    await fetch('/api/system/power/off', {method: 'POST'});
  };

  window.emergencyStop = async function() {
    await fetch('/api/system/emergency_stop', {method: 'POST'});
    const badge = document.getElementById('z21-status');
    if (badge) {
      badge.textContent = 'NOTHALT';
      badge.className = 'status-badge status-warning';
    }
  };

  // ── Start ────────────────────────────────────

  document.addEventListener('DOMContentLoaded', function() {
    connectWS();
  });

})();
