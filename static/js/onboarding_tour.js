(() => {
    const doneKey = 'viarms-onboarding-complete';
    const stepKey = 'viarms-onboarding-step';
    if (localStorage.getItem(doneKey) === 'true') return;

    const allSteps = [
        { key: 'dashboard', title: 'Your business overview', text: 'Start here to review sales, stock alerts, and daily performance.' },
        { key: 'pos', title: 'Make a sale', text: 'Use POS Checkout to search products, build a cart, take payment, and open the customer display.' },
        { key: 'products', title: 'Manage products', text: 'Add products, prices, stock quantities, categories, and expiry information here.' },
        { key: 'sales', title: 'Review sales', text: 'Use the Sales Log to find receipts, review transactions, and export reports.' },
        { key: 'staff', title: 'Manage your team', text: 'Add staff and assign the right role and shop access for each person.' },
        { key: 'shops', title: 'Manage branches', text: 'Create and manage your shop branches from this page.' },
        { key: 'settings', title: 'Make it yours', text: 'Finish by setting up your store details, receipts, taxes, and app theme.' },
    ].filter(step => document.querySelector(`[data-tour-key="${step.key}"]`));
    if (!allSteps.length) return;

    let index = Number(localStorage.getItem(stepKey) || 0);
    index = Math.min(Math.max(index, 0), allSteps.length - 1);
    const step = allSteps[index];
    const target = document.querySelector(`[data-tour-key="${step.key}"]`);

    const overlay = document.createElement('div');
    overlay.className = 'onboarding-overlay';
    overlay.innerHTML = `<section class="onboarding-card" role="dialog" aria-modal="true" aria-label="App tour">
        <span class="onboarding-progress">${index + 1} of ${allSteps.length}</span>
        <h2>${step.title}</h2><p>${step.text}</p>
        <p class="onboarding-hint">Look for the brightly highlighted menu item.</p>
        <div class="onboarding-actions"><button class="btn btn-secondary" type="button" data-tour-skip>Skip tour</button><button class="btn btn-primary" type="button" data-tour-next>${index === allSteps.length - 1 ? 'Finish tour' : 'Next'}</button></div>
    </section>`;
    document.body.appendChild(overlay);
    target.classList.add('tour-target');
    target.setAttribute('aria-current', 'step');

    const finish = () => {
        localStorage.setItem(doneKey, 'true');
        localStorage.removeItem(stepKey);
        target.classList.remove('tour-target');
        target.removeAttribute('aria-current');
        overlay.remove();
    };
    overlay.querySelector('[data-tour-skip]').addEventListener('click', finish);
    overlay.querySelector('[data-tour-next]').addEventListener('click', () => {
        if (index === allSteps.length - 1) return finish();
        localStorage.setItem(stepKey, String(index + 1));
        window.location.assign(document.querySelector(`[data-tour-key="${allSteps[index + 1].key}"]`).href);
    });
})();
