# More reliable reference-source discovery

Source discovery now bounds long sitemap scans and retries incomplete results
instead of treating a partial product list as complete. This keeps later
configured sources available when an earlier site is slow or publishes a
broken child sitemap. The existing approval and image-processing workflow is
unchanged. Connector and image-pipeline regression checks passed; restricted
third-party sites remain unverified rather than being reported as working.
