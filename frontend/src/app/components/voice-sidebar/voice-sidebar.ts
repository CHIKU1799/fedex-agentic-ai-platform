import { Component, signal, ViewChild, ElementRef, AfterViewInit } from '@angular/core';

const API_BASE = 'http://127.0.0.1:8000';

interface ChatMessage {
  role: 'assistant' | 'user';
  text: string;
  trackingCard?: { tracking_id: string; status: string; location: string; eta: string };
}

@Component({
  selector: 'app-voice-sidebar',
  standalone: true,
  templateUrl: './voice-sidebar.html',
  styleUrl: './voice-sidebar.css'
})
export class VoiceSidebarComponent implements AfterViewInit {
  isOpen = signal(false);
  voiceState = signal<'idle' | 'greeting' | 'listening' | 'processing' | 'speaking' | 'error'>('idle');
  messages = signal<ChatMessage[]>([]);
  errorMessage = signal('');

  // Secure by Design: explicit microphone consent. The mic is never opened
  // until the customer grants consent, and consent applies to this session
  // only. See grantConsent() / declineConsent().
  showConsent = signal(false);
  micConsentGranted = signal(false);

  @ViewChild('messagesContainer') messagesContainer!: ElementRef<HTMLDivElement>;

  // Whisper (MediaRecorder) state
  private mediaRecorder: MediaRecorder | null = null;
  private audioChunks: Blob[] = [];

  // Browser STT (SpeechRecognition) state
  private recognition: any = null;

  // false = browser STT, true = OpenAI Whisper; auto-detected on first use
  private useWhisper: boolean | null = null;

  // Auto-turn-off: if the mic is left listening with no speech, release it.
  // Re-armed on every speech event so it only fires on true inactivity.
  private inactivityTimer: any = null;
  private readonly INACTIVITY_MS = 15000;

  // End-of-utterance detection (browser STT): accumulate everything the user
  // says and only submit after a sustained silence, so mid-sentence pauses
  // never cut them off.
  private finalTranscript = '';
  private silenceTimer: any = null;
  private readonly SILENCE_MS = 2200;

  ngAfterViewInit() {}

  open(): void {
    this.messages.set([]);
    this.errorMessage.set('');
    this.isOpen.set(true);
    this.voiceState.set('greeting');

    const greeting = 'Hey, I am FedE. What can I help you with?';
    this.addMessage('assistant', greeting);
    this.voiceState.set('speaking');
    // After the greeting, ask for mic consent (once per session) before we
    // ever open the microphone. If already granted, start listening.
    this.speakText(greeting, () => {
      if (this.micConsentGranted()) {
        this.startListening();
      } else {
        this.voiceState.set('idle');
        this.showConsent.set(true);
      }
    });
  }

  // ── Microphone consent ────────────────────────────────────────

  grantConsent(): void {
    this.micConsentGranted.set(true);
    this.showConsent.set(false);
    this.startListening();
  }

  declineConsent(): void {
    this.showConsent.set(false);
    this.voiceState.set('idle');
    this.addMessage('assistant',
      'No problem — the microphone stays off. You can tap the mic button whenever you\'re ready.');
  }

  close(): void {
    this.finalTranscript = '';
    this.stopListening();
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    this.showConsent.set(false);
    this.isOpen.set(false);
    this.voiceState.set('idle');
    // Consent is per session and intentionally reset when the panel closes.
    this.micConsentGranted.set(false);
  }

  private armInactivityTimer(): void {
    this.clearInactivityTimer();
    this.inactivityTimer = setTimeout(() => {
      if (this.voiceState() === 'listening') {
        this.stopListening();
        this.voiceState.set('idle');
        this.addMessage('assistant', 'I turned the microphone off after a pause. Tap the mic to speak again.');
      }
    }, this.INACTIVITY_MS);
  }

  private clearInactivityTimer(): void {
    if (this.inactivityTimer) {
      clearTimeout(this.inactivityTimer);
      this.inactivityTimer = null;
    }
  }

  // ── End-of-utterance silence detection (browser STT) ──────────

  private armSilenceTimer(): void {
    this.clearSilenceTimer();
    this.silenceTimer = setTimeout(() => this.finishUtterance(), this.SILENCE_MS);
  }

  private clearSilenceTimer(): void {
    if (this.silenceTimer) {
      clearTimeout(this.silenceTimer);
      this.silenceTimer = null;
    }
  }

  /** Submit whatever the user has said so far. No-op if nothing was heard. */
  private finishUtterance(): void {
    const transcript = this.finalTranscript.trim();
    this.finalTranscript = '';
    if (!transcript) return; // nothing final yet — keep listening
    this.stopListening();
    this.voiceState.set('processing');
    this.processTextQuery(transcript);
  }

  toggleMic(): void {
    if (this.voiceState() === 'listening') {
      // Manual stop counts as "I'm done": submit anything already heard.
      if (this.finalTranscript.trim()) {
        this.finishUtterance();
        return;
      }
      this.stopListening();
      this.voiceState.set('idle');
    } else {
      if (this.voiceState() === 'speaking' && 'speechSynthesis' in window) {
        window.speechSynthesis.cancel();
      }
      // Gate every activation on consent, not just the first.
      if (!this.micConsentGranted()) {
        this.showConsent.set(true);
        return;
      }
      this.startListening();
    }
  }

  // ── Unified start/stop that picks the right mode ──────────────

  private async startListening(): Promise<void> {
    this.errorMessage.set('');

    // Hard guard: never open the mic without consent.
    if (!this.micConsentGranted()) {
      this.showConsent.set(true);
      return;
    }

    // First time: ask the backend whether Whisper STT is even configured, so
    // we never waste the user's first utterance on a doomed recording.
    if (this.useWhisper === null) {
      this.useWhisper = await this.whisperAvailable();
    }
    if (this.useWhisper) {
      await this.startMediaRecorder();
    } else {
      this.startBrowserSTT();
    }
    this.armInactivityTimer();
  }

  private async whisperAvailable(): Promise<boolean> {
    try {
      const res = await fetch(`${API_BASE}/health`);
      const data = await res.json();
      return !!data.stt_whisper;
    } catch {
      return false;
    }
  }

  private stopListening(): void {
    this.clearInactivityTimer();
    this.clearSilenceTimer();
    // Stop whichever is active — and always release the mic hardware.
    if (this.mediaRecorder?.state === 'recording') {
      this.voiceState.set('processing');
      this.mediaRecorder.stop();  // onstop stops the MediaStream tracks
    }
    const rec = this.recognition;
    this.recognition = null;
    if (rec) {
      try { rec.abort(); } catch { /* ignore */ }
    }
    if (this.voiceState() === 'listening') {
      this.voiceState.set('idle');
    }
  }

  // ── Mode 1: MediaRecorder → POST /voice (OpenAI Whisper STT) ──

  private async startMediaRecorder(): Promise<void> {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this.audioChunks = [];
      this.mediaRecorder = new MediaRecorder(stream);

      this.mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) this.audioChunks.push(e.data);
      };

      this.mediaRecorder.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        const blob = new Blob(this.audioChunks, { type: 'audio/webm' });
        this.voiceState.set('processing');
        await this.sendVoiceToBackend(blob);
      };

      this.mediaRecorder.start();
      this.voiceState.set('listening');
    } catch (err: unknown) {
      const isDenied = err instanceof Error && err.name === 'NotAllowedError';
      this.setError(
        isDenied
          ? 'Microphone access was denied. Please allow microphone access and try again.'
          : 'Unable to access microphone. Please check your device settings.'
      );
    }
  }

  private async sendVoiceToBackend(audioBlob: Blob): Promise<void> {
    const formData = new FormData();
    formData.append('file', audioBlob, 'voice.webm');

    try {
      const res = await fetch(`${API_BASE}/voice`, { method: 'POST', headers: this.authHeaders(), body: formData });
      if (!res.ok) throw new Error(`/voice returned ${res.status}`);

      const data: {
        text?: string;
        response?: { intent?: string; data?: Record<string, string> };
        error?: string;
      } = await res.json();

      // If Whisper STT failed (no API key / no credits), switch to browser STT
      if (data.error && (data.error.includes('STT Error') || data.error.includes('API'))) {
        console.warn('OpenAI Whisper unavailable, switching to browser speech recognition.');
        this.useWhisper = false;
        this.startBrowserSTT();
        return;
      }

      // Whisper works — lock in this mode
      if (this.useWhisper === null) this.useWhisper = true;

      // Empty / unclear speech input
      if (data.error || !data.text) {
        const msg = 'I could not understand that. Please try speaking again.';
        this.addMessage('assistant', msg);
        this.voiceState.set('speaking');
        this.speakText(msg, () => this.voiceState.set('idle'));
        return;
      }

      // Show transcribed text and handle response
      this.addMessage('user', data.text);
      this.handleResponse(data.response);

    } catch {
      // Network error or server down — fallback to browser STT
      if (this.useWhisper === null) {
        console.warn('Voice endpoint unreachable, switching to browser speech recognition.');
        this.useWhisper = false;
        this.startBrowserSTT();
        return;
      }
      const errMsg = 'Unable to connect to the voice service. Please try again later.';
      this.addMessage('assistant', errMsg);
      this.setError(errMsg);
    }
  }

  // ── Mode 2: Browser SpeechRecognition → POST /ask (free, no API key) ──

  private startBrowserSTT(): void {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) {
      this.setError('Speech recognition is not supported in this browser. Use Chrome or Edge.');
      return;
    }

    this.recognition = new SpeechRecognition();
    this.recognition.lang = 'en-US';
    this.recognition.continuous = true;
    // Interim results tell us the user is STILL speaking, so we never submit
    // mid-sentence — the query goes out only after SILENCE_MS of quiet.
    this.recognition.interimResults = true;
    this.recognition.maxAlternatives = 1;
    this.finalTranscript = '';

    this.recognition.onresult = (event: any) => {
      // Speech activity: the user is talking, so neither timer should fire.
      this.armInactivityTimer();
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (event.results[i].isFinal) {
          this.finalTranscript += event.results[i][0].transcript + ' ';
        }
      }
      // Any result (interim or final) restarts the end-of-speech clock.
      this.armSilenceTimer();
    };

    this.recognition.onerror = (event: any) => {
      if (event.error === 'not-allowed') {
        this.setError('Microphone access was denied. Please allow microphone access and try again.');
      } else if (event.error === 'no-speech') {
        try { this.recognition?.start(); } catch { /* already running */ }
      } else if (event.error === 'aborted') {
        // User or code stopped — do nothing
      } else {
        this.setError('Could not recognize speech. Tap mic to try again.');
      }
    };

    this.recognition.onend = () => {
      if (this.voiceState() === 'listening' && this.isOpen()) {
        // The browser sometimes ends recognition on its own. If the user had
        // already said something, treat that as the end of their turn;
        // otherwise seamlessly resume listening.
        if (this.finalTranscript.trim()) {
          this.finishUtterance();
          return;
        }
        try { this.recognition?.start(); } catch { /* ignore */ }
      }
    };

    this.recognition.start();
    this.voiceState.set('listening');
  }

  private async processTextQuery(text: string): Promise<void> {
    // Normalize spoken tracking IDs for browser STT
    let normalized = text.replace(/[.,!?]/g, '').trim();
    normalized = normalized.replace(/\bf\s*x\s*/gi, 'FX');
    const digitsOnly = normalized.match(/^(\d{3,})$/);
    if (digitsOnly) {
      normalized = `track FX${digitsOnly[1]}`;
    }
    normalized = normalized.replace(/\b(\d{6})\b/g, 'FX$1');

    this.addMessage('user', text);

    try {
      const res = await fetch(`${API_BASE}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...this.authHeaders() },
        body: JSON.stringify({ query: normalized })
      });
      if (!res.ok) throw new Error(`/ask returned ${res.status}`);

      const data = await res.json();
      this.handleResponse(data.response);
    } catch {
      const errMsg = 'Unable to connect to the service. Please try again later.';
      this.addMessage('assistant', errMsg);
      this.setError(errMsg);
    }
  }

  // ── Shared response handler ───────────────────────────────────

  private handleResponse(response: any): void {
    const d = response?.data || {};
    // Agentic replies carry their natural-language answer at the top level;
    // fallback-mode replies carry it inside data. Accept both.
    const agentText: string = (response?.response || '').trim();

    // Show a tracking card whenever the structured data looks like a shipment,
    // regardless of which path (agentic tool call or offline tracker) produced it.
    if (d.tracking_id && (d.status || d.location || d.eta)) {
      const card = {
        tracking_id: d.tracking_id,
        status: d.status || '',
        location: d.location || '',
        eta: d.eta || ''
      };
      const spoken = agentText ||
        `Your package with tracking ID ${card.tracking_id} is currently ${card.status}, located at ${card.location}. Estimated arrival is ${card.eta}.`;
      this.addMessage('assistant', agentText || 'I found your shipment. Here are the details:', card);
      this.voiceState.set('speaking');
      this.speakText(spoken, () => this.voiceState.set('idle'));
      return;
    }

    const reply = agentText || d.response || d.message ||
      'I could not find an answer to that. Please try rephrasing, or ask about a specific shipment.';
    this.addMessage('assistant', reply);
    this.voiceState.set('speaking');
    this.speakText(reply, () => this.voiceState.set('idle'));
  }

  // ── Helpers ───────────────────────────────────────────────────

  private addMessage(role: 'assistant' | 'user', text: string, trackingCard?: ChatMessage['trackingCard']): void {
    this.messages.update(msgs => [...msgs, { role, text, trackingCard }]);
    setTimeout(() => {
      if (this.messagesContainer) {
        this.messagesContainer.nativeElement.scrollTop = this.messagesContainer.nativeElement.scrollHeight;
      }
    }, 50);
  }

  // Attach the bearer token when the customer is signed in. Mutating
  // actions require it; anonymous callers can still track publicly.
  private authHeaders(): Record<string, string> {
    const token = (typeof localStorage !== 'undefined') ? localStorage.getItem('fede_token') : null;
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  /** Strip markdown/formatting so replies read naturally out loud. */
  private toSpeech(text: string): string {
    return text
      .replace(/[*_`#>]/g, '')          // markdown emphasis, code, headers
      .replace(/^\s*[-•]\s*/gm, '')      // bullet markers
      .replace(/\[(.*?)\]\(.*?\)/g, '$1') // links: keep label
      .replace(/\s+/g, ' ')
      .trim();
  }

  private speakText(text: string, onEnd?: () => void): void {
    if (!('speechSynthesis' in window)) { onEnd?.(); return; }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(this.toSpeech(text));
    utterance.rate = 0.95;
    utterance.pitch = 1;
    if (onEnd) utterance.onend = onEnd;
    window.speechSynthesis.speak(utterance);
  }

  private setError(message: string): void {
    this.errorMessage.set(message);
    this.voiceState.set('error');
    setTimeout(() => {
      this.errorMessage.set('');
      if (this.voiceState() === 'error') this.voiceState.set('idle');
    }, 6000);
  }

  getStatusBadgeClass(status: string): string {
    return 'fede-tracking-card__status-badge--' + (status || 'unknown').toLowerCase().replace(/\s+/g, '-');
  }

  get statusText(): string {
    switch (this.voiceState()) {
      case 'listening': return 'Listening...';
      case 'processing': return 'Processing...';
      case 'speaking': return 'Speaking...';
      case 'error': return 'Tap mic to retry.';
      default: return 'Tap mic to speak.';
    }
  }
}
