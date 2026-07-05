// tiny_fsm_reset: two-state Moore FSM with wrong reset state
// BUG: reset should drive state to S0, but here it goes to S1
module tiny_fsm(
    input clk,
    input rst,
    input in,
    output reg out
);
    parameter S0 = 1'b0;
    parameter S1 = 1'b1;

    reg state, next;

    always @(posedge clk) begin
        if (rst)
            state <= S1;   // BUG: should be S0
        else
            state <= next;
    end

    always @(*) begin
        case (state)
            S0: next = in ? S1 : S0;
            S1: next = in ? S0 : S1;
            default: next = S0;
        endcase
    end

    always @(*) begin
        out = (state == S1);
    end
endmodule
