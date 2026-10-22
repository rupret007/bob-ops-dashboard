"""The shared coordination bucket registry; provisioning remains operator-only."""

# role: (bucket name, TTL seconds); zero TTL is durable retry history.
BUCKETS = {
    'claims': ('STORYPOOL_CLAIMS', 300),
    'retries': ('STORYPOOL_CLAIMS_RETRIES', 0),
}


def bucket_name(role, claims_bucket=None):
    name = BUCKETS[role][0]
    if claims_bucket is None:
        return name
    # Isolated integration clients may choose another base without inventing
    # bucket names independently of this registry.
    return claims_bucket + name[len(BUCKETS['claims'][0]):]
