/**
 * Handle voting on questions with proper CSRF protection and dynamic updates.
 *
 * This function disables the button during the API call, sends the vote request,
 * updates the button appearance, and re-enables the button after the request is completed.
 *
 * @param {HTMLElement} button - The vote button element
 * @param {string} url - The URL to send the vote request to
 * @param {string} csrfToken - The CSRF token for secure requests
 */
function handleVote(button, url, csrfToken) {
  // Disable the button to prevent double-clicking
  button.disabled = true;

  // Send the fetch request
  fetch(url, {
    method: 'POST',
    headers: {
      'X-CSRFToken': csrfToken,
      'Content-Type': 'application/json'
    }
  }).then(response => {
    // Process HTML response if that's what we get (for htmx)
    const contentType = response.headers.get('content-type');
    if (contentType && contentType.includes('text/html')) {
      return response.text().then(html => {
        // Parse the HTML to get the question list
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');
        const questionList = doc.getElementById('question-list');

        if (questionList) {
          // Replace the current question list with the updated one
          const currentList = document.getElementById('question-list');
          if (currentList) {
            currentList.innerHTML = questionList.innerHTML;
          }
        }

        // Re-enable the button after HTML update
        button.disabled = false;
        return { html: true };
      });
    }

    // Handle JSON response
    return response.json().then(data => {
      // Re-enable the button
      button.disabled = false;

      // Update button appearance based on vote state
      if (data.user_voted) {
        button.classList.add('bg-blue-100', 'text-blue-600');
        button.classList.remove('bg-gray-100', 'hover:bg-gray-200');
      } else {
        button.classList.remove('bg-blue-100', 'text-blue-600');
        button.classList.add('bg-gray-100', 'hover:bg-gray-200');
      }

      // Update vote count
      const voteCountSpan = button.closest('div').querySelector('span.font-bold');
      if (voteCountSpan) {
        voteCountSpan.textContent = data.vote_count;
      }

      return data;
    });
  }).then(result => {
    // Trigger a custom event to notify other components about the vote
    document.body.dispatchEvent(new CustomEvent('vote-refresh'));
  }).catch(error => {
    // Re-enable the button in case of error
    button.disabled = false;
    console.error('Error voting:', error);
  });
}

/**
 * Handle filtering of questions and preserve form content.
 *
 * @param {string} value - The filter value
 * @param {string} baseUrl - The base URL for the talk questions
 */
function applyFilter(value, baseUrl) {
  // Preserve the question input field value before navigation
  const questionInput = document.getElementById('id_content');
  if (questionInput) {
    sessionStorage.setItem('question_content', questionInput.value);
  }
  window.location.href = baseUrl + "?status_filter=" + value;
}

/**
 * Clear the question input field after submission.
 * This function should be called after a successful form submission.
 */
function clearQuestionInput() {
  // Clear any saved question content from sessionStorage
  sessionStorage.removeItem('question_content');

  // Clear the input field
  const questionInput = document.getElementById('id_content');
  if (questionInput) {
    questionInput.value = '';
  }
}
