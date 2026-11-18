let cart = [];
let activeCategory = 'all';
let searchQuery = '';
let lastAddedProductId = null;
let posBarcodeScanner = null;
let posScannerStartPromise = null;
const customerDisplayChannel = 'BroadcastChannel' in window ? new BroadcastChannel('viARMS_customer_display') : null;

// --- Hardware Barcode Scanner (USB/Bluetooth Keyboard Wedge) ---
// Scanner input emits keys in rapid succession (< 50ms) followed by Enter.
let hwBarcodeBuffer = "";
let hwBarcodeLastTime = 0;
const HW_SCANNER_MAX_INTERVAL = 60; // ms between consecutive keystrokes from a scanner

function initHardwareBarcodeScanner() {
    window.addEventListener("keydown", function (e) {
        // Do not intercept if camera scanner modal or receipt modal is open
        const cameraModal = document.getElementById("pos-barcode-scanner-modal");
        const receiptModal = document.getElementById("receipt-modal");
        if ((cameraModal && cameraModal.classList.contains("active")) ||
            (receiptModal && receiptModal.classList.contains("active"))) {
            return;
        }

        const now = Date.now();
        const activeElem = document.activeElement;
        const isEditingCartDiscount = activeElem && activeElem.id === "cart-discount";

        // If user is editing cart discount or selecting payment method, let them type
        if (isEditingCartDiscount) return;

        // If Enter key is pressed, evaluate buffer or searchInput
        if (e.key === "Enter") {
            const timeDiff = now - hwBarcodeLastTime;
            const barcodeCandidate = hwBarcodeBuffer.trim();

            // Check if buffer contains a scanned barcode (scanned rapidly or has length >= 2)
            if (barcodeCandidate.length >= 2 && timeDiff < 400) {
                e.preventDefault();
                e.stopPropagation();
                hwBarcodeBuffer = "";
                hwBarcodeLastTime = 0;
                handleHardwareScannedBarcode(barcodeCandidate);
                return;
            }

            // If focused on search-input, handle standard Enter press
            if (activeElem && activeElem.id === "search-input") {
                const queryVal = activeElem.value.trim();
                if (queryVal) {
                    e.preventDefault();
                    handleHardwareScannedBarcode(queryVal);
                    activeElem.value = "";
                    searchQuery = "";
                    renderCatalog();
                }
            }
            hwBarcodeBuffer = "";
            return;
        }

        // Ignore modifier keys, functional keys, tab, escape, etc.
        if (e.key.length !== 1 || e.ctrlKey || e.altKey || e.metaKey) {
            return;
        }

        const charTimeDiff = now - hwBarcodeLastTime;
        // If elapsed time is small, it's very likely from the attached hardware scanner
        if (hwBarcodeBuffer.length === 0 || charTimeDiff <= HW_SCANNER_MAX_INTERVAL) {
            hwBarcodeBuffer += e.key;
            hwBarcodeLastTime = now;
        } else {
            // Slower keystroke - start a new potential barcode sequence
            hwBarcodeBuffer = e.key;
            hwBarcodeLastTime = now;
        }

        // If the scanner typed into the search-input, prevent duplicates when enter arrives
        // and keep search-input focused by default for cashier convenience
    }, true);
}

function handleHardwareScannedBarcode(rawBarcode) {
    const code = rawBarcode.trim();
    if (!code) return;

    // Pulse badge to indicate hardware scan detected
    const badge = document.getElementById("hw-scanner-badge");
    if (badge) {
        badge.style.background = "rgba(16, 185, 129, 0.25)";
        badge.style.color = "var(--color-success)";
        badge.style.borderColor = "var(--color-success)";
        badge.innerHTML = `<i data-lucide="check" style="width: 13px; height: 13px;"></i> Scanned: ${escapeHtml(code)}`;
        lucide.createIcons();
        setTimeout(() => {
            badge.style.background = "rgba(99, 102, 241, 0.15)";
            badge.style.color = "var(--color-primary)";
            badge.style.borderColor = "rgba(99, 102, 241, 0.35)";
            badge.innerHTML = `<i data-lucide="barcode" style="width: 13px; height: 13px;"></i> Scanner Ready`;
            lucide.createIcons();
        }, 1800);
    }

    // Lookup product in productsData
    searchForScannedBarcode(code);
}

document.addEventListener("DOMContentLoaded", function () {
    // Check network status and sync
    window.addEventListener('online', updateNetworkStatus);
    window.addEventListener('offline', updateNetworkStatus);
    
    initDexieDB().then(() => {
        if (navigator.onLine && typeof productsData !== 'undefined') {
            cacheCatalogToDexie(productsData, defaultTaxRate);
        }
        updateNetworkStatus();
        checkUnsyncedCount();
    });

    renderCatalog();
    initHardwareBarcodeScanner();
    
    // Bind search inputs
    const searchInput = document.getElementById('search-input');
    if (searchInput) {
        // Automatically focus search input when page loads so scanner is ready immediately
        setTimeout(() => searchInput.focus(), 150);

        searchInput.addEventListener('input', function (e) {
            searchQuery = e.target.value.toLowerCase().trim();
            renderCatalog();
        });
    }

    document.getElementById('pos-scan-button').addEventListener('click', startPosBarcodeScanner);
    document.getElementById('pos-scan-close').addEventListener('click', stopPosBarcodeScanner);
    document.getElementById('pos-scan-cancel').addEventListener('click', stopPosBarcodeScanner);

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

function startPosBarcodeScanner() {
    if (typeof Html5Qrcode === 'undefined') {
        showNotification('Barcode scanning is unavailable because the scanner could not be loaded.', 'error');
        return;
    }

    const modal = document.getElementById('pos-barcode-scanner-modal');
    const error = document.getElementById('pos-scan-error');
    error.style.display = 'none';
    modal.classList.add('active');

    const scanner = new Html5Qrcode('pos-barcode-reader');
    posBarcodeScanner = scanner;
    let barcodeHandled = false;
    posScannerStartPromise = scanner.start(
        { facingMode: 'environment' },
        { fps: 10, qrbox: { width: 250, height: 150 } },
        decodedText => {
            if (barcodeHandled || posBarcodeScanner !== scanner) return;
            barcodeHandled = true;
            stopPosBarcodeScanner();
            searchForScannedBarcode(decodedText);
        },
        () => {}
    ).then(() => {
        if (posBarcodeScanner !== scanner && scanner.isScanning) {
            return scanner.stop();
        }
    }).catch(cameraError => {
        console.error('Barcode scanner could not start:', cameraError);
        if (posBarcodeScanner === scanner) {
            posBarcodeScanner = null;
            error.textContent = 'Could not access the camera. Allow camera permission and use a supported browser on HTTPS or localhost.';
            error.style.display = 'block';
            showNotification(error.textContent, 'error');
        }
    }).finally(() => {
        if (posBarcodeScanner === scanner) posScannerStartPromise = null;
    });
}

function stopPosBarcodeScanner() {
    const scanner = posBarcodeScanner;
    const startPromise = posScannerStartPromise;
    posBarcodeScanner = null;
    document.getElementById('pos-barcode-scanner-modal').classList.remove('active');
    if (!scanner) return;

    const stopPromise = startPromise
        ? startPromise.then(() => scanner.isScanning ? scanner.stop() : undefined)
        : scanner.isScanning ? scanner.stop() : Promise.resolve();
    stopPromise
        .then(() => scanner.clear())
        .catch(cameraError => {
            console.error('Barcode scanner could not stop cleanly:', cameraError);
        });
}

function playBarcodeBeep() {
    try {
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const oscillator = audioCtx.createOscillator();
        const gainNode = audioCtx.createGain();
        oscillator.type = "sine";
        oscillator.frequency.setValueAtTime(1750, audioCtx.currentTime); // 1.75kHz POS scanner beep
        gainNode.gain.setValueAtTime(0.12, audioCtx.currentTime);
        gainNode.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.08);
        oscillator.connect(gainNode);
        gainNode.connect(audioCtx.destination);
        oscillator.start();
        oscillator.stop(audioCtx.currentTime + 0.08);
    } catch (e) {
        // AudioContext not allowed or not supported in environment
    }
}

function searchForScannedBarcode(decodedText) {
    const barcode = decodedText.trim();
    if (!barcode) return;

    const searchInput = document.getElementById('search-input');
    // Match against SKU or barcode field (case-insensitive)
    const matchedProduct = productsData.find(product =>
        (product.sku && product.sku.toLowerCase() === barcode.toLowerCase()) ||
        (product.batch_number && product.batch_number.toLowerCase() === barcode.toLowerCase())
    );

    if (activeCategory !== 'all') {
        activeCategory = 'all';
        document.querySelectorAll('.category-chip').forEach(chip => {
            chip.classList.toggle('active', chip.getAttribute('data-category-id') === 'all');
        });
    }

    if (matchedProduct) {
        playBarcodeBeep();
        addToCart(matchedProduct.id);
        if (searchInput) {
            searchInput.value = '';
            searchQuery = '';
            searchInput.focus();
        }
        renderCatalog();
    } else {
        if (searchInput) {
            searchInput.value = barcode;
            searchQuery = barcode.toLowerCase();
            searchInput.focus();
        }
        renderCatalog();
        showNotification(`No product found for scanned code: "${barcode}"`, 'error');
    }
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, character => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    })[character]);
}

// Render catalog grid
function renderCatalog() {
    const catalog = document.getElementById('products-catalog');
    const catalogCount = document.getElementById('catalog-count');
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
        if (catalogCount) catalogCount.textContent = '0 products';
        catalog.innerHTML = `
            <div class="pos-empty-state">
                <i data-lucide="search-x" aria-hidden="true"></i>
                <strong>No products found</strong>
                <span>Try another product name, SKU, or category.</span>
            </div>
        `;
        lucide.createIcons();
        return;
    }

    if (catalogCount) {
        catalogCount.textContent = `${filtered.length} product${filtered.length === 1 ? '' : 's'}`;
    }

    filtered.forEach(product => {
        const productName = escapeHtml(product.name);
        const productSku = escapeHtml(product.sku);
        const categoryName = escapeHtml(product.category_name);
        const isOutOfStock = product.stock_quantity <= 0;
        const card = document.createElement('div');
        card.className = `product-card glass-panel ${isOutOfStock ? 'out-of-stock' : ''}`;
        card.setAttribute('role', 'button');
        card.setAttribute('tabindex', isOutOfStock ? '-1' : '0');
        card.setAttribute('aria-label', isOutOfStock ? `${product.name}, out of stock` : `Add ${product.name} to cart`);
        
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
                expiryWarning = `<div style="font-size: 0.72rem; color: #f59e0b; font-weight: 600; margin-top: 3px; display: flex; align-items: center; gap: 3px;"><i data-lucide="calendar-off" style="width:12px; height:12px;"></i>Expiring: ${escapeHtml(product.expiry_date)}</div>`;
            }
        }

        let expiryLabel = '';
        if (product.expiry_date && product.expiry_date !== 'No Expiry') {
            expiryLabel = `<div style="font-size: 0.72rem; color: var(--text-secondary); margin-top: 3px; display: flex; align-items: center; gap: 3px;"><i data-lucide="calendar" style="width:12px; height:12px;"></i>Exp: ${escapeHtml(product.expiry_date)}</div>`;
        }

        let pharmaBadge = '';
        let pharmaSubtext = '';
        let businessSubtext = '';
        if (product.generic_name || product.dosage_form || product.strength) {
            let descStr = '';
            if (product.generic_name) descStr += `<strong>${escapeHtml(product.generic_name)}</strong>`;
            if (product.dosage_form) descStr += ` (${escapeHtml(product.dosage_form)})`;
            if (product.strength) descStr += ` - ${escapeHtml(product.strength)}`;

            let extraInfo = [];
            if (product.manufacturer) extraInfo.push(escapeHtml(product.manufacturer));
            if (product.batch_number) extraInfo.push(`B/N: ${escapeHtml(product.batch_number)}`);
            if (product.nafdac_reg_no) extraInfo.push(`NAFDAC: ${escapeHtml(product.nafdac_reg_no)}`);
            if (product.storage_conditions) extraInfo.push(`[${escapeHtml(product.storage_conditions)}]`);

            pharmaSubtext = `
                <div style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 4px; border-top: 1px solid rgba(255,255,255,0.05); padding-top: 4px; line-height: 1.2;">
                    ${descStr}
                    ${extraInfo.length > 0 ? `<div style="font-size: 0.7rem; color: var(--text-muted); margin-top: 2px;">${extraInfo.join(' | ')}</div>` : ''}
                </div>
            `;

            if (product.prescription_required) {
                pharmaBadge = `<span class="badge badge-danger" style="font-size: 0.65rem; padding: 0.1rem 0.3rem; margin-left: 6px;">Rx</span>`;
            } else {
                pharmaBadge = `<span class="badge badge-info" style="font-size: 0.65rem; padding: 0.1rem 0.3rem; margin-left: 6px; opacity: 0.7; background: rgba(16, 185, 129, 0.2); color: #10b981; border-color: transparent;">OTC</span>`;
            }
        }

        const productDetails = [];
        if (product.author) productDetails.push(`Author: ${escapeHtml(product.author)}`);
        if (product.publisher) productDetails.push(`Publisher: ${escapeHtml(product.publisher)}`);
        if (product.isbn) productDetails.push(`ISBN: ${escapeHtml(product.isbn)}`);
        if (product.color) productDetails.push(`Color: ${escapeHtml(product.color)}`);
        if (product.size) productDetails.push(`Size: ${escapeHtml(product.size)}`);
        if (product.warranty_period) productDetails.push(`Warranty: ${escapeHtml(product.warranty_period)}`);
        if (product.material_display) productDetails.push(`Material: ${escapeHtml(product.material_display)}`);
        if (productDetails.length) {
            businessSubtext = `<div class="product-card-business-details">${productDetails.join(' · ')}</div>`;
        }

        card.innerHTML = `
            <div class="product-card-layout">
                ${product.image_url ? `<img class="product-card-image" src="${escapeHtml(product.image_url)}" alt="${productName}" loading="lazy">` : ''}
                <div class="product-card-details">
                    <div class="product-card-information">
                        <div class="product-card-name" style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap;">
                            <span>${productName}</span>
                            ${pharmaBadge}
                        </div>
                        <div class="product-card-sku">SKU: ${productSku}</div>
                        <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 2px;">${categoryName}</div>
                        ${expiryLabel}
                        ${expiryWarning}
                        ${pharmaSubtext}
                        ${businessSubtext}
                    </div>
                    <div class="product-card-summary">
                        <span class="product-card-price">₦${parseFloat(product.selling_price).toFixed(2)}</span>
                        <span class="product-card-stock" style="color: ${stockColor}; font-weight: 500;">${stockLabel}</span>
                    </div>
                </div>
            </div>
        `;

        if (!isOutOfStock) {
            card.addEventListener('click', () => addToCart(product.id));
            card.addEventListener('keydown', event => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    addToCart(product.id);
                }
            });
        }
        catalog.appendChild(card);
    });
    lucide.createIcons();
}

// Add item to shopping cart
function addToCart(productId) {
    const product = productsData.find(p => p.id === productId);
    if (!product || product.stock_quantity <= 0) return;

    lastAddedProductId = productId;
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
            strength: product.strength,
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

            let nameHTML = item.name;
            if (item.strength) {
                nameHTML += ` <span style="font-size: 0.75rem; color: var(--text-muted); font-weight: normal;">(${item.strength})</span>`;
            }

            row.innerHTML = `
                <div class="cart-item-details">
                    <div class="cart-item-name">${nameHTML}</div>
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

    // Persist the current cart so a display opened after scanning has started
    // can render immediately. BroadcastChannel keeps updates instant.
    const displayPayload = {
        type: 'cart_update',
        items: cart,
        subtotal: subtotal,
        discount: discount,
        tax: taxAmount,
        grand_total: grandTotal,
        last_added_id: lastAddedProductId
    };
    localStorage.setItem('viARMS_customer_cart', JSON.stringify(displayPayload));

    // Broadcast cart update to secondary customer screen
    if (customerDisplayChannel) {
        customerDisplayChannel.postMessage(displayPayload);
    }
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
        
        saveOfflineSaleToDexie(localReceiptNumber, payload).then(() => {
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

// --- Offline & Sync Support with Dexie.js ---
let posDb = null;

function initDexieDB() {
    if (typeof Dexie === 'undefined') {
        console.warn('[Dexie] Dexie library not loaded, falling back to basic cache');
        return Promise.resolve(null);
    }

    if (posDb) return Promise.resolve(posDb);

    posDb = new Dexie('viARMS_POS_PWA_DB');
    posDb.version(1).stores({
        products: 'id, sku, name, category_id, stock_quantity',
        settings: 'key',
        offlineSales: 'local_receipt_number, timestamp, synced'
    });

    return posDb.open().then(() => {
        console.log('[Dexie] Database opened successfully');
        return posDb;
    }).catch(err => {
        console.error('[Dexie] Failed to open database:', err);
        return null;
    });
}

async function cacheCatalogToDexie(catalog, taxRate) {
    try {
        const db = await initDexieDB();
        if (!db) return;

        // Upsert all products into Dexie
        if (Array.isArray(catalog) && catalog.length > 0) {
            await db.products.bulkPut(catalog);
        }
        await db.settings.put({ key: 'tax_rate', value: taxRate });
        await db.settings.put({ key: 'last_cached', value: new Date().toISOString() });
    } catch (e) {
        console.warn('[Dexie] Error caching catalog:', e);
    }
}

async function getOfflineSalesCount() {
    try {
        const db = await initDexieDB();
        if (!db) return 0;
        return await db.offlineSales.where('synced').equals(0).count();
    } catch (e) {
        console.error('[Dexie] Error counting offline sales:', e);
        return 0;
    }
}

async function saveOfflineSaleToDexie(localReceiptNumber, payload) {
    const db = await initDexieDB();
    if (!db) throw new Error('IndexedDB not available');

    await db.offlineSales.put({
        local_receipt_number: localReceiptNumber,
        payload: payload,
        timestamp: new Date().toISOString(),
        synced: 0
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
    const count = await getOfflineSalesCount();
    updateSyncBadge(count);
}

let isSyncing = false;
async function syncOfflineSales() {
    if (isSyncing || !navigator.onLine) return;
    isSyncing = true;

    try {
        const db = await initDexieDB();
        if (!db) return;

        const pending = await db.offlineSales.where('synced').equals(0).toArray();
        updateSyncBadge(pending.length);
        if (pending.length === 0) return;

        showNotification(`Syncing ${pending.length} offline invoice(s) to server...`, 'info');

        for (const sale of pending) {
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
                    // Mark as synced and delete from pending queue
                    await db.offlineSales.delete(sale.local_receipt_number);
                }
            } catch (err) {
                console.error('[Sync] Failed to sync sale', sale.local_receipt_number, err);
                break; // Stop loop if network breaks during loop
            }
        }

        const remaining = await getOfflineSalesCount();
        updateSyncBadge(remaining);
        if (remaining === 0) {
            showNotification('All offline sales synced successfully with Django!', 'success');
        }
    } catch (e) {
        console.error('[Sync] Error in sync process', e);
    } finally {
        isSyncing = false;
    }
}

// Listen for service worker background sync messages
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', (event) => {
        if (event.data && event.data.type === 'TRIGGER_BACKGROUND_SYNC') {
            console.log('[PWA] Service Worker triggered background sync');
            syncOfflineSales();
        }
    });
}
