# Demo service architecture

The demo service separates payment processing, configuration loading, catalog indexing, and operational scripts. Each component owns a narrow API and its nearest behavioral tests.

Requests enter through an application layer, call domain services, and emit structured events. Configuration is resolved before services start. Catalog data is independent from payment gateway state. Operational scripts must support Windows paths without assuming the current directory.

This document intentionally contains broad project vocabulary. A focused context selector should prefer implementation and tests whose paths and contents match the complete query.
