# Safer delivery of a photo editor update

The Windows image workflow can retrieve a fixed version of its photo-editing
package over its existing authenticated connection. The download includes an
integrity digest and does not install or run the editor automatically, keeping
the owner's visual approval step intact.

HTTP integration tests cover access control and package integrity. Installation
and rendered-image quality on the owner's computer still need live verification.
