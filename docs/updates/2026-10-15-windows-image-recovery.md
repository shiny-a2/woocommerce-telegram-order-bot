# Recovering a missing Windows image input

The product image workflow can now reoffer an original image to its existing
Windows editing job when the local input file is lost after download. Recovery
checks the job's product identity and preserves the manual editing and send
steps, so a repeated request does not create a second product or image job.

Loopback integration tests covered input loss, redelivery, duplicate requests,
and mismatched product identities. Delivery to the owner's PC and final store
publication remain separate live verification steps.
