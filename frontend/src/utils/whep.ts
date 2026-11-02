// Minimal WHEP (WebRTC-HTTP egress) client for MediaMTX: one POST with the SDP offer, the answer comes back.

const GATHER_TIMEOUT_MS = 2000;

function waitForIceGathering(pc: RTCPeerConnection): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      pc.removeEventListener("icegatheringstatechange", check);
      window.clearTimeout(timer);
      resolve();
    };
    const check = () => pc.iceGatheringState === "complete" && done();
    const timer = window.setTimeout(done, GATHER_TIMEOUT_MS);
    pc.addEventListener("icegatheringstatechange", check);
  });
}

export interface WhepSession {
  stream: MediaStream;
  close: () => void;
}

/**
 * Connects to `whepUrl`. Resolves once the answer is applied (the video arrives on `stream` shortly after).
 * `onLost` fires if the connection fails or drops later.
 */
export async function connectWhep(whepUrl: string, onLost: () => void): Promise<WhepSession> {
  const pc = new RTCPeerConnection();
  const stream = new MediaStream();
  let resource: string | null = null;
  let closed = false;

  const close = () => {
    if (closed) return;
    closed = true;
    pc.close();
    if (resource) void fetch(resource, { method: "DELETE" }).catch(() => undefined);
  };

  pc.addTransceiver("video", { direction: "recvonly" });
  pc.ontrack = (e) => stream.addTrack(e.track);
  pc.onconnectionstatechange = () => {
    if (!closed && (pc.connectionState === "failed" || pc.connectionState === "disconnected")) {
      close();
      onLost();
    }
  };

  try {
    await pc.setLocalDescription(await pc.createOffer());
    await waitForIceGathering(pc);
    const response = await fetch(whepUrl, {
      method: "POST",
      headers: { "Content-Type": "application/sdp" },
      body: pc.localDescription?.sdp,
    });
    if (!response.ok) throw new Error(`WHEP ${response.status}`);
    const location = response.headers.get("Location");
    if (location) resource = new URL(location, whepUrl).toString();
    await pc.setRemoteDescription({ type: "answer", sdp: await response.text() });
  } catch (error) {
    close();
    throw error;
  }
  if (closed) throw new Error("closed while connecting");
  return { stream, close };
}
