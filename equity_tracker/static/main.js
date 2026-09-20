// Get Stripe publishable key
const stripeScript = document.currentScript;
const stripeConfigUrl = stripeScript?.dataset.stripeConfigUrl;
const createCheckoutSessionUrl = stripeScript?.dataset.createCheckoutSessionUrl;

if (stripeConfigUrl && createCheckoutSessionUrl) {
  fetch(stripeConfigUrl)
    .then((result) => { return result.json(); })
    .then((data) => {
      // Initialize Stripe.js
      const stripe = Stripe(data.publicKey);

      // Event handler
      const submitBtn = document.querySelector("#submitBtn");
      if (submitBtn !== null) {
        submitBtn.addEventListener("click", () => {
          // Get Checkout Session ID
          fetch(createCheckoutSessionUrl)
            .then((result) => { return result.json(); })
            .then((data) => {
              // Redirect to Stripe Checkout
              return stripe.redirectToCheckout({ sessionId: data.sessionId });
            });
        });
      }
    });
}
