let cart = [];
let activeCategory = 'all';
let searchQuery = '';

document.addEventListener("DOMContentLoaded", function () {
    // Check network status and sync
    window.addEventListener('online', updateNetworkStatus);
    window.addEventListener('offline', updateNetworkStatus);
    
    initDB().then(() => {
        // Cache data for offline mode
        if (navigator.onLine) {
            saveToStore('products_cache', 'catalog', productsData);
            saveToStore('settings_cache', 'tax_rate', defaultTaxRate);
        }
        updateNetworkStatus();
        checkUnsyncedCount();
    });

    renderCatalog();
    
    // Bind search inputs
    const searchInput = document.getElementById('search-input');
    searchInput.addEventListener('input', function (e) {
        searchQuery = e.target.value.toLowerCase().trim();
        renderCatalog();
    });

    // Check if barcode scanner (fast enter key) is used
    searchInput.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            const matchedProduct = productsData.find(p => p.sku.toLowerCase() === searchQuery);
            if (matchedProduct) {
                addToCart(matchedProduct.id);
                searchInput.value = '';
                searchQuery = '';
                renderCatalog();
            }
        }
    });

    // Bind category chips
    const chips = document.querySelectorAll('.category-chip');
    chips.forEach(chip => {
        chip.addEventListener('click', function () {
            chips.forEach(c => c.classList.remove('active'));
            this.classList.add('active');
            activeCategory = this.getAttribute('data-category-id');
            renderCatalog();
        });
    });

    // Bind checkout discount and details calculations
    document.getElementById('cart-discount').addEventListener('input', updateTotals);
    document.getElementById('checkout-btn').addEventListener('click', submitCheckout);
});

// Render catalog grid
function renderCatalog() {
    const catalog = document.getElementById('products-catalog');
    catalog.innerHTML = '';

    const filtered = productsData.filter(product => {
        // Category filter
        const matchCategory = activeCategory === 'all' || String(product.category_id) === String(activeCategory);
        // Search filter (name or SKU)
        const matchSearch = product.name.toLowerCase().includes(searchQuery) || 
                            product.sku.toLowerCase().includes(searchQuery);
        return matchCategory && matchSearch;
    });

    if (filtered.length === 0) {
        catalog.innerHTML = `
            <div style="grid-column: 1/-1; text-align: center; color: var(--text-muted); padding: 2rem;">
                No products found matching the criteria.
            </div>
        `;
        return;
    }

    filtered.forEach(product => {
        const isOutOfStock = product.stock_quantity <= 0;
        const card = document.createElement('div');
        card.className = `product-card glass-panel ${isOutOfStock ? 'out-of-stock' : ''}`;
        
        let stockLabel = `${product.stock_quantity} in stock`;
        let stockColor = 'var(--text-secondary)';
        let expiryWarning = '';

        if (isOutOfStock) {
            stockLabel = 'OUT OF STOCK';
            stockColor = 'var(--color-danger)';
        } else {
            if (product.is_low_stock) {
                stockLabel = `Low stock (${product.stock_quantity} left)`;
                stockColor = 'var(--color-warning)';
            }
            if (product.is_expiring_soon) {
                expiryWarning = `<div style="font-size: 0.72rem; color: #f59e0b; font-weight: 600; margin-top: 3px; display: flex; align-items: center; gap: 3px;"><i data-lucide="calendar-off" style="width:12px; height:12px;"></i>Expiring: ${product.expiry_date}</div>`;
            }
        }

        card.innerHTML = `
            <div>
                <div class="product-card-name">${product.name}</div>
                <div class="product-card-sku">SKU: ${product.sku}</div>
                <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 2px;">${product.category_name}</div>
                ${expiryWarning}
            </div>
            <div style="margin-top: auto; display: flex; justify-content: space-between; align-items: flex-end;">
                <span class="product-card-price">₦${parseFloat(product.selling_price).toFixed(2)}</span>
                <span class="product-card-stock" style="color: ${stockColor}; font-weight: 500;">${stockLabel}</span>
            </div>
        `;

        if (!isOutOfStock) {
            card.addEventListener('click', () => addToCart(product.id));
        }
        catalog.appendChild(card);
    });
    lucide.createIcons();
}

// Add item to shopping cart
function addToCart(productId) {
    const product = productsData.find(p => p.id === productId);
    if (!product || product.stock_quantity <= 0) return;

    const cartItem = cart.find(item => item.product_id === productId);

    if (cartItem) {
        if (cartItem.qty + 1 > product.stock_quantity) {
            showNotification(`Cannot add more. Only ${product.stock_quantity} available in inventory.`, 'error');
            return;
        }
        cartItem.qty += 1;
    } else {
        cart.push({
            product_id: product.id,
            name: product.name,
            price: parseFloat(product.selling_price),
            qty: 1,
            max_stock: product.stock_quantity
        });
    }

    updateCartUI();
    showNotification(`Added ${product.name} to cart.`);
}

// Update Cart HTML and recalculate numbers
function updateCartUI() {
    const list = document.getElementById('cart-items-list');
    const emptyMsg = document.getElementById('cart-empty-message');
    const countBadge = document.getElementById('cart-count-badge');
    const checkoutBtn = document.getElementById('checkout-btn');

    // Remove old cart item rows (keep empty message placeholder if empty)
    const existingItems = list.querySelectorAll('.cart-item');
    existingItems.forEach(item => item.remove());

    if (cart.length === 0) {
        emptyMsg.style.display = 'flex';
        countBadge.textContent = '0 items';
        checkoutBtn.disabled = true;
    } else {
        emptyMsg.style.display = 'none';
        countBadge.textContent = `${cart.reduce((sum, item) => sum + item.qty, 0)} items`;
        checkoutBtn.disabled = false;

        cart.forEach(item => {
            const row = document.createElement('div');
            row.className = 'cart-item';
            row.innerHTML = `
                <div class="cart-item-details">
                    <div class="cart-item-name">${item.name}</div>
                    <div class="cart-item-price">₦${item.price.toFixed(2)}</div>
                </div>
                <div class="cart-item-qty-control">
                    <button class="qty-btn" onclick="adjustQty(${item.product_id}, -1)">-</button>
                    <span style="font-weight: 600; min-width: 20px; text-align: center;">${item.qty}</span>
                    <button class="qty-btn" onclick="adjustQty(${item.product_id}, 1)">+</button>
                </div>
                <div class="cart-item-subtotal">₦${(item.price * item.qty).toFixed(2)}</div>
                <button class="remove-cart-item" onclick="removeFromCart(${item.product_id})">
                    <i data-lucide="trash-2" style="width: 14px; height: 14px; vertical-align: middle;"></i>
                </button>
            `;
            list.appendChild(row);
        });
        lucide.createIcons();
    }
    
    updateTotals();
}

// Adjust quantity inside cart
function adjustQty(productId, amount) {
    const item = cart.find(i => i.product_id === productId);
    if (!item) return;

    if (amount > 0 && item.qty + 1 > item.max_stock) {
        showNotification(`Insufficient stock. Only ${item.max_stock} available in stock.`, 'error');
        return;
    }

    item.qty += amount;
    if (item.qty <= 0) {
        removeFromCart(productId);
    } else {
        updateCartUI();
    }
}

// Remove item entirely from cart
function removeFromCart(productId) {
    cart = cart.filter(item => item.product_id !== productId);
    updateCartUI();
}

// Perform calculations for payment block
function updateTotals() {
    const subtotal = cart.reduce((sum, item) => sum + (item.price * item.qty), 0);
    
    const discountInput = document.getElementById('cart-discount');
    let discount = parseFloat(discountInput.value) || 0;
    
    if (discount < 0) {
        discount = 0;
        discountInput.value = '0.00';
    }
    if (discount > subtotal) {
        discount = subtotal;
        discountInput.value = subtotal.toFixed(2);
    }

    const taxableAmount = Math.max(0, subtotal - discount);
    const taxAmount = taxableAmount * (defaultTaxRate / 100);
    const grandTotal = taxableAmount + taxAmount;

    document.getElementById('summary-subtotal').textContent = `₦${subtotal.toFixed(2)}`;
    document.getElementById('summary-discount').textContent = `-₦${discount.toFixed(2)}`;
    document.getElementById('summary-tax').textContent = `₦${taxAmount.toFixed(2)}`;
    document.getElementById('summary-total').textContent = `₦${grandTotal.toFixed(2)}`;
}

// Submit payment payload to checkout API
function submitCheckout() {
    if (cart.length === 0) return;

    const discount = parseFloat(document.getElementById('cart-discount').value) || 0;
    const paymentMethod = document.getElementById('payment-method').value;
    const checkoutBtn = document.getElementById('checkout-btn');

    checkoutBtn.disabled = true;
    checkoutBtn.innerHTML = `<i data-lucide="loader" class="animate-spin" style="width: 14px; height: 14px; animation: spin 1s linear infinite;"></i> Processing...`;
    lucide.createIcons();

    const payload = {
        items: cart.map(item => ({
            product_id: item.product_id,
            quantity: item.qty
        })),
        discount: discount,
        payment_method: paymentMethod
    };

    if (!navigator.onLine) {
        // Offline processing
        const localReceiptNumber = 'OFFLINE-' + Date.now();
        payload.local_receipt_number = localReceiptNumber;
        
        saveToStore('offline_sales_queue', localReceiptNumber, {
            local_receipt_number: localReceiptNumber,
            payload: payload,
            timestamp: new Date().toISOString()
        }).then(() => {
            showNotification('Saved offline. Will sync when connection is restored.', 'success');
            checkUnsyncedCount();
            
            // Adjust local catalog stock
            cart.forEach(item => {
                const catalogProd = productsData.find(p => p.id === item.product_id);
                if (catalogProd) {
                    catalogProd.stock_quantity -= item.qty;
                    catalogProd.is_low_stock = catalogProd.stock_quantity <= catalogProd.min_stock_level;
                }
            });

            // Mock a receipt
            const subtotal = cart.reduce((sum, item) => sum + (item.price * item.qty), 0);
            const taxableAmount = Math.max(0, subtotal - discount);
            const taxAmount = taxableAmount * (defaultTaxRate / 100);
            
            const mockReceipt = {
                store_name: "viARMS (Offline)",
                store_address: "Pending Sync",
                store_phone: "",
                receipt_number: localReceiptNumber,
                timestamp: new Date().toLocaleString(),
                cashier: "Current User",
                subtotal: subtotal.toFixed(2),
                discount: discount.toFixed(2),
                tax_rate: defaultTaxRate.toFixed(1),
                tax_amount: taxAmount.toFixed(2),
                total_amount: (taxableAmount + taxAmount).toFixed(2),
                payment_method: paymentMethod,
                receipt_footer: "Thank you! (Offline Receipt)",
                items: cart.map(item => ({
                    name: item.name,
                    quantity: item.qty,
                    subtotal: (item.price * item.qty).toFixed(2)
                }))
            };
            
            displayReceiptModal(mockReceipt);
            
            // Reset cart
            cart = [];
            document.getElementById('cart-discount').value = '0.00';
            updateCartUI();
            renderCatalog();
        }).catch(err => {
            console.error(err);
            showNotification('Failed to save offline sale.', 'error');
        }).finally(() => {
            checkoutBtn.disabled = false;
            checkoutBtn.innerHTML = `<i data-lucide="check-circle"></i> Complete Checkout`;
            lucide.createIcons();
        });
        return;
    }

    fetch(checkoutUrl, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
        },
        body: JSON.stringify(payload)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showNotification('Checkout completed successfully!', 'success');
            
            // Adjust local catalog stock levels dynamically to prevent re-clicks without full page refresh
            cart.forEach(item => {
                const catalogProd = productsData.find(p => p.id === item.product_id);
                if (catalogProd) {
                    catalogProd.stock_quantity -= item.qty;
                    catalogProd.is_low_stock = catalogProd.stock_quantity <= catalogProd.min_stock_level;
                }
            });

            // Display printed receipt modal
            displayReceiptModal(data.receipt);

            // Reset cart
            cart = [];
            document.getElementById('cart-discount').value = '0.00';
            updateCartUI();
            renderCatalog();
        } else {
            showNotification(data.error || 'Checkout failed. Please try again.', 'error');
        }
    })
    .catch(err => {
        console.error(err);
        showNotification('System connection error. Checkout could not be processed.', 'error');
    })
    .finally(() => {
        checkoutBtn.disabled = false;
        checkoutBtn.innerHTML = `<i data-lucide="check-circle"></i> Complete Checkout`;
        lucide.createIcons();
    });
}

// Display printed receipt model
function displayReceiptModal(receipt) {
    const area = document.getElementById('receipt-print-area');
    
    let itemsRows = '';
    receipt.items.forEach(item => {
        itemsRows += `
            <div class="receipt-item-row">
                <span>${item.name} (x${item.quantity})</span>
                <span>₦${parseFloat(item.subtotal).toFixed(2)}</span>
            </div>
        `;
    });

    area.innerHTML = `
        <div class="receipt-header">
            <h4 style="margin: 0; font-size: 1.1rem; font-weight: bold;">${receipt.store_name}</h4>
            <div style="font-size: 0.75rem; color: #333; margin: 4px 0;">${receipt.store_address}</div>
            <div style="font-size: 0.75rem; color: #333;">Tel: ${receipt.store_phone}</div>
        </div>
        
        <div style="font-size: 0.75rem; margin-bottom: 0.5rem;">
            <div>Receipt: ${receipt.receipt_number}</div>
            <div>Date: ${receipt.timestamp}</div>
            <div>Cashier: ${receipt.cashier}</div>
        </div>
        
        <div class="receipt-divider"></div>
        ${itemsRows}
        <div class="receipt-divider"></div>
        
        <div class="receipt-item-row" style="font-size: 0.8rem;">
            <span>Subtotal</span>
            <span>₦${parseFloat(receipt.subtotal).toFixed(2)}</span>
        </div>
        <div class="receipt-item-row" style="font-size: 0.8rem;">
            <span>Discount</span>
            <span>-₦${parseFloat(receipt.discount).toFixed(2)}</span>
        </div>
        <div class="receipt-item-row" style="font-size: 0.8rem;">
            <span>Tax (${parseFloat(receipt.tax_rate).toFixed(1)}%)</span>
            <span>₦${parseFloat(receipt.tax_amount).toFixed(2)}</span>
        </div>
        <div class="receipt-item-row" style="font-size: 1rem; font-weight: bold; margin-top: 4px;">
            <span>Total Paid</span>
            <span>₦${parseFloat(receipt.total_amount).toFixed(2)}</span>
        </div>
        
        <div class="receipt-divider"></div>
        <div style="font-size: 0.8rem;">Method: ${receipt.payment_method}</div>
        <div class="receipt-divider"></div>
        
        <div style="text-align: center; font-size: 0.75rem; margin-top: 1rem; font-style: italic;">
            ${receipt.receipt_footer}
        </div>
    `;

    document.getElementById('receipt-modal').classList.add('active');
}

function closeReceiptModal() {
    document.getElementById('receipt-modal').classList.remove('active');
}

// Print trigger
function printReceipt() {
    const printContent = document.getElementById('receipt-print-area').innerHTML;
    const originalContent = document.body.innerHTML;

    // Standard print flow: open print window
    const printWindow = window.open('', '', 'height=600,width=400');
    printWindow.document.write('<html><head><title>Print Receipt</title>');
    printWindow.document.write('<style>');
    printWindow.document.write(`
        body { font-family: 'Courier New', monospace; padding: 20px; color: black; background: white; }
        .receipt-header { text-align: center; margin-bottom: 15px; border-bottom: 1px dashed black; padding-bottom: 8px; }
        .receipt-divider { border-bottom: 1px dashed black; margin: 10px 0; }
        .receipt-item-row { display: flex; justify-content: space-between; margin: 5px 0; font-size: 13px; }
    `);
    printWindow.document.write('</style></head><body>');
    printWindow.document.write(printContent);
    printWindow.document.write('</body></html>');
    printWindow.document.close();
    printWindow.print();
}

// --- Offline & Sync Support (IndexedDB) ---
const dbName = 'viARMS_POS_DB';
const dbVersion = 1;

function initDB() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(dbName, dbVersion);
        request.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains('products_cache')) {
                db.createObjectStore('products_cache');
            }
            if (!db.objectStoreNames.contains('settings_cache')) {
                db.createObjectStore('settings_cache');
            }
            if (!db.objectStoreNames.contains('offline_sales_queue')) {
                db.createObjectStore('offline_sales_queue', { keyPath: 'local_receipt_number' });
            }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

function saveToStore(storeName, key, data) {
    return initDB().then(db => {
        return new Promise((resolve, reject) => {
            const tx = db.transaction(storeName, 'readwrite');
            const store = tx.objectStore(storeName);
            if (key) {
                store.put(data, key);
            } else {
                store.put(data);
            }
            tx.oncomplete = () => resolve();
            tx.onerror = () => reject(tx.error);
        });
    });
}

function getAllFromStore(storeName) {
    return initDB().then(db => {
        return new Promise((resolve, reject) => {
            const tx = db.transaction(storeName, 'readonly');
            const store = tx.objectStore(storeName);
            const request = store.getAll();
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    });
}

function deleteFromStore(storeName, key) {
    return initDB().then(db => {
        return new Promise((resolve, reject) => {
            const tx = db.transaction(storeName, 'readwrite');
            const store = tx.objectStore(storeName);
            store.delete(key);
            tx.oncomplete = () => resolve();
            tx.onerror = () => reject(tx.error);
        });
    });
}

function updateNetworkStatus() {
    const badge = document.getElementById('network-status-badge');
    if (!badge) return;
    if (navigator.onLine) {
        badge.style.background = 'rgba(16,185,129,0.15)';
        badge.style.color = 'var(--color-success)';
        badge.style.borderColor = 'rgba(16,185,129,0.3)';
        badge.innerHTML = '<i data-lucide="wifi" style="width: 12px; height: 12px; display: inline-block; vertical-align: middle;"></i> Online';
        syncOfflineSales();
    } else {
        badge.style.background = 'rgba(239,68,68,0.15)';
        badge.style.color = 'var(--color-danger)';
        badge.style.borderColor = 'rgba(239,68,68,0.3)';
        badge.innerHTML = '<i data-lucide="wifi-off" style="width: 12px; height: 12px; display: inline-block; vertical-align: middle;"></i> Offline';
    }
    lucide.createIcons();
}

function updateSyncBadge(count) {
    const badge = document.getElementById('unsynced-badge');
    if (!badge) return;
    if (count > 0) {
        badge.style.display = 'inline-block';
        badge.innerHTML = `<i data-lucide="refresh-cw" style="width: 12px; height: 12px; display: inline-block; vertical-align: middle;"></i> Unsynced: ${count}`;
    } else {
        badge.style.display = 'none';
    }
    lucide.createIcons();
}

async function checkUnsyncedCount() {
    try {
        const queue = await getAllFromStore('offline_sales_queue');
        updateSyncBadge(queue.length);
    } catch (e) {
        console.error("Error reading queue count", e);
    }
}

async function syncOfflineSales() {
    try {
        const queue = await getAllFromStore('offline_sales_queue');
        updateSyncBadge(queue.length);
        if (queue.length === 0) return;

        for (const sale of queue) {
            try {
                const response = await fetch(checkoutUrl, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken
                    },
                    body: JSON.stringify(sale.payload)
                });
                const data = await response.json();
                if (data.success || (data.error && data.error.includes('already synced'))) {
                    await deleteFromStore('offline_sales_queue', sale.local_receipt_number);
                }
            } catch (err) {
                console.error("Failed to sync sale", sale.local_receipt_number, err);
            }
        }
        checkUnsyncedCount();
    } catch (e) {
        console.error("Error in sync process", e);
    }
}
