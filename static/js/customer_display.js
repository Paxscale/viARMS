/**
 * customer_display.js
 * Listens on the viARMS BroadcastChannel and renders live cart data
 * on the customer-facing display screen. No server requests needed.
 */

const fmt = (n) => '₦' + parseFloat(n || 0).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',');

const idleScreen   = document.getElementById('idle-screen');
const activeScreen = document.getElementById('active-screen');
const footerEl     = document.getElementById('display-footer');
const itemsEl      = document.getElementById('cart-items-display');

// Last-added product id for highlight animation
let lastAddedId = null;

function renderCart(data) {
    const { items = [], subtotal = 0, discount = 0, tax = 0, grand_total = 0 } = data;

    if (!items.length) {
        idleScreen.style.display   = 'flex';
        activeScreen.style.display = 'none';
        footerEl.style.display     = 'none';
        return;
    }

    idleScreen.style.display   = 'none';
    activeScreen.style.display = 'flex';
    footerEl.style.display     = 'block';

    // Render items
    itemsEl.innerHTML = '';
    items.forEach(item => {
        const div = document.createElement('div');
        div.className = 'display-item' + (item.product_id === lastAddedId ? ' highlight' : '');
        div.innerHTML = `
            <div>
                <div class="item-name">${item.name}</div>
                <div class="item-unit">${fmt(item.price)} each</div>
            </div>
            <div class="item-qty">×${item.qty}</div>
            <div class="item-subtotal">${fmt(item.price * item.qty)}</div>
        `;
        itemsEl.appendChild(div);
    });

    // Scroll to bottom so latest item is visible
    itemsEl.scrollTop = itemsEl.scrollHeight;

    // Totals
    document.getElementById('disp-subtotal').textContent = fmt(subtotal);

    const discountBlock = document.getElementById('disp-discount-block');
    if (discount > 0) {
        discountBlock.style.display = 'block';
        document.getElementById('disp-discount').textContent = '-' + fmt(discount);
    } else {
        discountBlock.style.display = 'none';
    }

    const taxBlock = document.getElementById('disp-tax-block');
    if (tax > 0) {
        taxBlock.style.display = 'block';
        document.getElementById('disp-tax').textContent = fmt(tax);
    } else {
        taxBlock.style.display = 'none';
    }

    const grandEl = document.getElementById('disp-grand');
    grandEl.textContent = fmt(grand_total);

    // Pop animation on grand total change
    grandEl.classList.remove('pop');
    requestAnimationFrame(() => {
        requestAnimationFrame(() => grandEl.classList.add('pop'));
    });
    setTimeout(() => grandEl.classList.remove('pop'), 500);
}

// BroadcastChannel listener
if ('BroadcastChannel' in window) {
    const channel = new BroadcastChannel('viARMS_customer_display');
    channel.addEventListener('message', (event) => {
        if (event.data && event.data.type === 'cart_update') {
            lastAddedId = event.data.last_added_id || null;
            renderCart(event.data);
        }
        if (event.data && event.data.type === 'cart_cleared') {
            lastAddedId = null;
            renderCart({ items: [] });
        }
    });
} else {
    // Fallback: show a notice that BroadcastChannel isn't supported (old browser)
    document.getElementById('idle-screen').querySelector('.idle-sub').textContent =
        'Live display not supported on this browser. Please use Chrome or Firefox.';
}

// A display can be opened after scanning has already started. Restore the
// latest snapshot, and use storage events as a cross-tab fallback.
try {
    const savedCart = localStorage.getItem('viARMS_customer_cart');
    if (savedCart) {
        const data = JSON.parse(savedCart);
        lastAddedId = data.last_added_id || null;
        renderCart(data);
    }
} catch (error) {
    console.warn('Unable to restore the customer display cart.', error);
}

window.addEventListener('storage', event => {
    if (event.key !== 'viARMS_customer_cart' || !event.newValue) return;
    try {
        const data = JSON.parse(event.newValue);
        lastAddedId = data.last_added_id || null;
        renderCart(data);
    } catch (error) {
        console.warn('Unable to update the customer display cart.', error);
    }
});
