<?php
declare(strict_types=1);

interface ProviderForwarder {
    public function finish(string $sessionDirectory, array $metadata): array;
}

final class StoredOnlyForwarder implements ProviderForwarder {
    public function finish(string $sessionDirectory, array $metadata): array {
        return ['status' => 'stored', 'transcript' => null, 'provider' => null];
    }
}

