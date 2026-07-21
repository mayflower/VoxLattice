#!/usr/bin/env python3
"""LiveKit Agents example using one RemoteFastEnhancer per input track."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, room_io
from livekit.plugins import fastenhancer

load_dotenv()


def new_processor() -> fastenhancer.RemoteFastEnhancer:
    client_certificate = os.environ.get("FASTENHANCER_CLIENT_CERTIFICATE")
    client_private_key = os.environ.get("FASTENHANCER_CLIENT_PRIVATE_KEY")
    return fastenhancer.RemoteFastEnhancer(
        endpoint=os.environ.get("FASTENHANCER_ENDPOINT", "dns:///fastenhancer:50051"),
        api_key=os.environ["FASTENHANCER_API_TOKEN"],
        tls=os.environ.get("FASTENHANCER_TLS", "true").lower() == "true",
        root_certificates=(
            Path(os.environ["FASTENHANCER_ROOT_CERTIFICATE"]).read_bytes()
            if "FASTENHANCER_ROOT_CERTIFICATE" in os.environ
            else None
        ),
        client_certificate_chain=(
            Path(client_certificate).read_bytes() if client_certificate else None
        ),
        client_private_key=(Path(client_private_key).read_bytes() if client_private_key else None),
    )


def select_processor(
    params: room_io.NoiseCancellationParams,
) -> fastenhancer.RemoteFastEnhancer:
    """The selector is invoked per track and always returns a fresh state owner."""
    del params
    return new_processor()


server = AgentServer()


@server.rtc_session(agent_name="fastenhancer-example")
async def entrypoint(ctx: agents.JobContext) -> None:
    use_selector = os.environ.get("FASTENHANCER_USE_SELECTOR", "true").lower() == "true"
    processor = select_processor if use_selector else new_processor()
    session = AgentSession()
    await session.start(
        room=ctx.room,
        agent=Agent(instructions="Process incoming voice audio without storing it."),
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                sample_rate=16_000,
                num_channels=1,
                frame_size_ms=32,
                noise_cancellation=processor,
                auto_gain_control=False,
            )
        ),
        record=False,
    )


if __name__ == "__main__":
    agents.cli.run_app(server)
