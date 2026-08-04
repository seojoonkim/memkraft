store_core.append writes without sort_keys, so its bytes are not canonical bytes. Digesting the file line would be a different value.
