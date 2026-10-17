# Safer delivery of a photo editor update

The Windows image workflow can retrieve a fixed version of its photo-editing
package over its existing authenticated connection. The download includes an
integrity digest and does not install or run the editor automatically, keeping
the owner's visual approval step intact.

A newer diagnostic candidate can be downloaded alongside the previous package.
The available versions are fixed, and requesting an unknown version fails.

HTTP integration tests cover access control and package integrity. Installation
and rendered-image quality on the owner's computer still need live verification.

The Telegram intake was also restored after its process stopped while a
different process kept the old watchdog check satisfied. The health check now
tracks the bot itself. Headerless image workbooks can safely use either
reference/brand or brand/reference order; existing items retain their queue
identity when the same file is submitted again.
