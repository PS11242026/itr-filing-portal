"""Repositories. Convention (test-enforced): every public function's first
parameter is either `scope: TaxpayerScope` (taxpayer-scoped data) or
`principal: Principal` (global/admin operations, staff-checked internally).
Raw taxpayer_id values are never accepted from callers.
"""
