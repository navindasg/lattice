"""Voice command panel with 3D mic button and transcript display.

The mic button has a Duolingo-inspired 3D raised look using layered
borders and color transitions for idle/recording/processing states.
Clickable and bound to push-to-talk hotkey.
"""
from __future__ import annotations

from enum import Enum

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Button, Input, RichLog, Static


class MicState(str, Enum):
    """Visual states for the mic button."""
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"


class MicButton(Button):
    """3D-style microphone button inspired by Duolingo.

    Uses layered border styling and color transitions to create a
    raised, tactile appearance.  Three visual states: idle (blue),
    recording (red), processing (amber).
    """

    DEFAULT_CSS = """
    MicButton {
        width: 100%;
        min-width: 16;
        height: 5;
        margin: 1 2;
        text-style: bold;
        border-top: tall $panel-lighten-3;
        border-bottom: tall $panel-darken-3;
        content-align: center middle;
    }

    MicButton.mic-idle {
        background: #1a8fe3;
        color: #ffffff;
        border-top: tall #4db8ff;
        border-bottom: tall #0d5a94;
        border-left: tall #2da0f0;
        border-right: tall #1270b5;
    }

    MicButton.mic-idle:hover {
        background: #2da0f0;
        border-top: tall #66c4ff;
        border-bottom: tall #1270b5;
        border-left: tall #40b0ff;
        border-right: tall #1a8fe3;
    }

    MicButton.mic-idle:focus {
        background: #2da0f0;
        border-top: tall #66c4ff;
        border-bottom: tall #1270b5;
    }

    MicButton.mic-recording {
        background: #e63946;
        color: #ffffff;
        border-top: tall #ff6b78;
        border-bottom: tall #8b1a24;
        border-left: tall #f04e5c;
        border-right: tall #c42d38;
        text-style: bold reverse;
    }

    MicButton.mic-processing {
        background: #e6a817;
        color: #1a1a2e;
        border-top: tall #ffc942;
        border-bottom: tall #8b6500;
        border-left: tall #f0b82a;
        border-right: tall #c48f10;
    }
    """

    mic_state: reactive[MicState] = reactive(MicState.IDLE)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._update_label()

    def _update_label(self) -> None:
        """Set button label and CSS class based on mic state."""
        labels = {
            MicState.IDLE: "  \U0001f3a4  Push to Talk  ",
            MicState.RECORDING: "  \U0001f534  Recording...  ",
            MicState.PROCESSING: "  \u23f3  Processing...  ",
        }
        self.label = labels.get(self.mic_state, labels[MicState.IDLE])

    def watch_mic_state(self) -> None:
        """Update visual state when mic_state changes."""
        self.remove_class("mic-idle", "mic-recording", "mic-processing")
        self.add_class(f"mic-{self.mic_state.value}")
        self._update_label()

    def on_mount(self) -> None:
        """Set initial state class."""
        self.add_class("mic-idle")


class VoicePanel(Widget):
    """Sidebar panel for voice commands: mic button, transcript, text input.

    Emits VoicePanel.TextSubmitted when the user types a command.
    Emits VoicePanel.MicToggled when the mic button is clicked.
    """

    DEFAULT_CSS = """
    VoicePanel {
        height: auto;
        max-height: 50%;
        width: 100%;
        padding: 0;
    }

    VoicePanel .voice-header {
        dock: top;
        height: 1;
        background: $primary;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }

    VoicePanel .voice-transcript {
        height: 1fr;
        min-height: 6;
        max-height: 20;
        border: round $surface-lighten-1;
        margin: 0 1;
        padding: 0 1;
    }

    VoicePanel .voice-input {
        margin: 0 1 1 1;
    }
    """

    class TextSubmitted(Message):
        """Fired when the user submits text via the input field."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class MicToggled(Message):
        """Fired when the mic button is clicked or hotkey pressed."""

    def compose(self) -> ComposeResult:
        yield Static(" \U0001f50a Voice Commands", classes="voice-header")
        yield MicButton(id="mic-btn")
        yield VerticalScroll(
            RichLog(id="voice-log", highlight=True, markup=True, wrap=True),
            classes="voice-transcript",
        )
        yield Input(
            placeholder="Type a command...",
            id="voice-text-input",
            classes="voice-input",
        )

    @property
    def mic_button(self) -> MicButton:
        """Access the mic button widget."""
        return self.query_one("#mic-btn", MicButton)

    @property
    def voice_log(self) -> RichLog:
        """Access the voice transcript log."""
        return self.query_one("#voice-log", RichLog)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle mic button click."""
        if event.button.id == "mic-btn":
            self.post_message(self.MicToggled())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle text input submission."""
        if event.input.id == "voice-text-input" and event.value.strip():
            self.post_message(self.TextSubmitted(event.value.strip()))
            event.input.value = ""

    def set_mic_state(self, state: MicState) -> None:
        """Update the mic button visual state.

        Args:
            state: New MicState (IDLE, RECORDING, PROCESSING).
        """
        self.mic_button.mic_state = state

    def append_transcript(self, text: str) -> None:
        """Append a line to the voice command transcript.

        Args:
            text: Formatted transcript entry to display.
        """
        self.voice_log.write(text)

    def append_result(self, transcript: str, action: str, detail: str) -> None:
        """Append a formatted voice command result to the transcript.

        Args:
            transcript: The original voice/text input.
            action: The classified action name.
            detail: Action detail or response summary.
        """
        self.voice_log.write(
            f'[bold cyan]>[/bold cyan] "{transcript}" '
            f"[dim]\u2192[/dim] [bold]{action}[/bold]: {detail}"
        )
