# Reference-source verification: fourth batch

Five more configured watch-reference domains were checked on the live bot
host. Two sites explicitly require manual review, one rate-limited requests,
and one denied access. The fifth had a readable exact product page but its
current automatic sitemap route did not discover it, so it remains unverified
as an automatic source.

The update improves image extraction when that retailer's exact page is known:
its product-image preload is recognized, a model name containing `Icon` no
longer triggers the site-furniture filter, and empty-sitemap diagnostics work
on Windows service consoles. It does not bypass site restrictions or change
source priority, spreadsheet approval, Windows processing, or publication.
Twenty of 44 configured domains have been checked; the remaining domains
still need live samples.
