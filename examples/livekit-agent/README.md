# LiveKit Agent example

This example uses current `RoomOptions`/`AudioInputOptions` APIs and explicitly
requests 16-kHz mono, 32-ms frames. The selector returns a fresh processor for
every track; never share an instance because it owns one remote model state and
one absolute timeline.

AEC may remain enabled at the capture edge. Disable browser/frontend neural
noise suppression so FastEnhancer is the only heavy denoiser. Local Compose is
the only documented reason to set `FASTENHANCER_TLS=false`; distributed use must
use TLS. `record=False` prevents LiveKit Agents observability from recording
audio in this privacy-preserving example.

For mTLS, set `FASTENHANCER_ROOT_CERTIFICATE`,
`FASTENHANCER_CLIENT_CERTIFICATE`, and `FASTENHANCER_CLIENT_PRIVATE_KEY` to
mounted PEM files. Never place the private key in the image or repository.
