# More reliable Romanson image lookup

The Romanson image source now uses the retailer's public product search rather
than an incomplete sitemap route. A current reference returned an exact
product match and two validated images from the production host. Existing
approval, image processing and publishing steps are unchanged. This verifies
one representative lookup, not the whole catalog or an end-to-end upload.
